"""
AI PR Investigator — Backend Server
=====================================
FastAPI application that receives GitHub webhook events.

Endpoints:
    POST /webhooks/github   — Receives and verifies GitHub webhook deliveries.
    GET  /health            — Simple health check.

Usage:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Required environment variables:
    GITHUB_WEBHOOK_SECRET   — The secret configured in the GitHub App webhook settings.
"""

import json
import logging
import sys

from fastapi import BackgroundTasks, FastAPI, Request, Response, status

from pydantic import ValidationError

from app.domain.models import PullRequest
from app.integrations.github.client import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)
from app.integrations.github.mapper import GitHubPullRequestMapper
from app.integrations.github.service import get_installation_client
from app.integrations.github.webhook import (
    HANDLED_PR_ACTIONS,
    extract_pr_info,
    verify_signature,
)
from app.integrations.jira.bridge import BridgeResult, GitHubJiraBridge
from app.integrations.jira.client import (
    JiraAuthenticationError,
    JiraClient,
    JiraClientError,
    JiraConfigurationError,
    JiraIssueNotFoundError,
    JiraNetworkError,
)
from app.integrations.jira.key_extractor import extract_jira_key
from app.integrations.jira.mapper import JiraRequirementMapper
from app.settings import GITHUB_WEBHOOK_SECRET, JIRA_BASE_URL

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

# Configure logging — safe output only, never secrets.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-10s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("server")

# ---------------------------------------------------------------------------
# Validate required env vars at startup
# ---------------------------------------------------------------------------

if not GITHUB_WEBHOOK_SECRET:
    logger.error("GITHUB_WEBHOOK_SECRET is not set. Exiting.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI PR Investigator",
    description="GitHub webhook receiver for AI PR Investigator.",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok"}


def get_jira_bridge() -> GitHubJiraBridge:
    """
    Construct a configured GitHubJiraBridge instance.

    Uses application settings (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN).
    """
    client = JiraClient()
    mapper = JiraRequirementMapper()
    return GitHubJiraBridge(
        jira_client=client,
        mapper=mapper,
        jira_base_url=JIRA_BASE_URL,
    )


class ProcessedPRContext(dict):
    """
    Result of PR context retrieval, mapping, and Jira requirement resolution.

    Inherits from dict for full backward compatibility with callers and tests expecting
    the raw GitHub context dictionary, while exposing the mapped PullRequest and
    BridgeResult models via attributes and the raw_context property.

    Design Decision:
      The underlying dictionary contains strictly the original raw GitHub context fields,
      preserving raw payload dictionary equality (context == raw_context) and JSON
      serialization compatibility (json.dumps(context)). Mapped domain models (PullRequest,
      BridgeResult) are exposed explicitly as attributes and properties, preventing domain
      models from contaminating raw dictionary keys or breaking JSON serializers.
    """

    def __init__(
        self,
        raw_context: dict,
        pull_request: PullRequest | None = None,
        bridge_result: BridgeResult | None = None,
    ) -> None:
        super().__init__(raw_context)
        self.pull_request = pull_request
        self.bridge_result = bridge_result

    @property
    def raw_context(self) -> dict:
        """Returns the original raw GitHub context dictionary."""
        return dict(self)



def process_pull_request_context(
    pr_info: dict,
    mapper: GitHubPullRequestMapper | None = None,
    bridge: GitHubJiraBridge | None = None,
) -> ProcessedPRContext | dict | None:
    """
    Retrieve and process PR context asynchronously in a background task.

    1. Obtains authenticated GitHubClient via get_installation_client(installation_id).
    2. Retrieves raw PR context (metadata, changed files with patches, commits).
    3. Maps raw context to canonical PullRequest domain model via GitHubPullRequestMapper.
    4. Resolves linked Jira Requirement via GitHubJiraBridge using PR title and description.
    5. Returns ProcessedPRContext bundling raw context, PullRequest, and BridgeResult.

    Security & Reliability:
      - Traps all authentication, API, network, validation, and unexpected errors without crashing.
      - Never logs tokens, JWTs, keys, patches, or credentials.
      - Gracefully handles PRs with no Jira key or unavailable Jira API.

    Args:
        pr_info: Dictionary containing validated PR identifiers:
                 'installation_id', 'owner', 'repo', 'pull_number'.
        mapper: Optional GitHubPullRequestMapper override (defaults to new instance).
        bridge: Optional GitHubJiraBridge override (defaults to get_jira_bridge()).

    Returns:
        ProcessedPRContext dictionary containing mapped models and raw data,
        or None if retrieval failed.
    """
    installation_id = pr_info.get("installation_id")
    owner = pr_info.get("owner")
    repo = pr_info.get("repo")
    pull_number = pr_info.get("pull_number")

    try:
        client = get_installation_client(installation_id)
        context = client.get_pull_request_context(owner, repo, pull_number)

        files_count = len(context.get("files", []))
        commits_count = len(context.get("commits", []))

        logger.info(
            "PR context successfully retrieved for %s/%s PR #%d (installation=%d): %d files, %d commits",
            owner,
            repo,
            pull_number,
            installation_id,
            files_count,
            commits_count,
        )

        # Step 2: Convert raw context to canonical PullRequest domain model
        pull_request: PullRequest | None = None
        try:
            pr_mapper = mapper or GitHubPullRequestMapper()
            pull_request = pr_mapper.to_pull_request(context, owner, repo)
            logger.info(
                "Successfully mapped %s/%s PR #%d to canonical PullRequest domain model: '%s' (%s -> %s) by %s",
                owner,
                repo,
                pull_number,
                pull_request.title,
                pull_request.source_branch,
                pull_request.target_branch,
                pull_request.author,
            )
        except (ValueError, ValidationError) as exc:
            logger.warning(
                "Could not map context for %s/%s PR #%d into canonical PullRequest: %s",
                owner,
                repo,
                pull_number,
                exc,
            )

        # Step 3: Resolve Jira requirement via GitHubJiraBridge
        bridge_result: BridgeResult | None = None
        if pull_request is not None:
            # Check whether a Jira key is present before constructing JiraClient/Bridge
            jira_key = extract_jira_key(pull_request.title, pull_request.description)
            if jira_key is None and bridge is None:
                bridge_result = BridgeResult(
                    requirement=None,
                    jira_key=None,
                    reason="No Jira key found in PR title or body.",
                )
                logger.info(
                    "No Jira requirement resolved for %s/%s PR #%d: %s",
                    owner,
                    repo,
                    pull_number,
                    bridge_result.reason,
                )
            else:
                try:
                    jira_bridge = bridge or get_jira_bridge()
                    bridge_result = jira_bridge.get_requirement_for_pr(
                        pr_title=pull_request.title,
                        pr_body=pull_request.description,
                    )
                    if bridge_result.has_requirement:
                        logger.info(
                            "Jira requirement resolved for %s/%s PR #%d: requirement_id=%s",
                            owner,
                            repo,
                            pull_number,
                            bridge_result.requirement.requirement_id,
                        )
                    else:
                        logger.info(
                            "No Jira requirement resolved for %s/%s PR #%d: %s",
                            owner,
                            repo,
                            pull_number,
                            bridge_result.reason,
                        )
                except JiraAuthenticationError:
                    logger.error(
                        "Jira authentication failed while resolving requirement for %s/%s PR #%d",
                        owner,
                        repo,
                        pull_number,
                    )
                except JiraIssueNotFoundError as exc:
                    logger.warning(
                        "Jira issue not found for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        exc,
                    )
                except JiraNetworkError as exc:
                    logger.error(
                        "Network error communicating with Jira API for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        exc,
                    )
                except JiraConfigurationError as exc:
                    logger.warning(
                        "Jira configuration unavailable while resolving requirement for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        exc,
                    )
                except JiraClientError as exc:
                    logger.error(
                        "Jira API error for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        exc,
                    )
                except Exception as exc:
                    logger.error(
                        "Unexpected error while resolving Jira requirement for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        type(exc).__name__,
                    )

        # Step 4: Make resulting PullRequest and BridgeResult available to next stage
        return ProcessedPRContext(
            raw_context=context,
            pull_request=pull_request,
            bridge_result=bridge_result,
        )

    except GitHubAuthenticationError:
        logger.error(
            "GitHub authentication failed for installation %s while fetching %s/%s PR #%s",
            installation_id,
            owner,
            repo,
            pull_number,
        )
        return None
    except GitHubNotFoundError:
        logger.warning(
            "Pull request #%s not found in %s/%s (installation %s)",
            pull_number,
            owner,
            repo,
            installation_id,
        )
        return None
    except GitHubPermissionError:
        logger.error(
            "GitHub API permission denied or rate limited for %s/%s PR #%s (installation %s)",
            owner,
            repo,
            pull_number,
            installation_id,
        )
        return None
    except GitHubNetworkError as exc:
        logger.error(
            "Network error communicating with GitHub API for %s/%s PR #%s: %s",
            owner,
            repo,
            pull_number,
            exc,
        )
        return None
    except GitHubClientError as exc:
        logger.error(
            "GitHub API error for %s/%s PR #%s: %s",
            owner,
            repo,
            pull_number,
            exc,
        )
        return None
    except Exception as exc:
        logger.error(
            "Unexpected error while processing PR context for %s/%s PR #%s: %s",
            owner,
            repo,
            pull_number,
            type(exc).__name__,
        )
        return None


@app.post("/webhooks/github", status_code=status.HTTP_200_OK)
async def github_webhook(request: Request, background_tasks: BackgroundTasks = None):
    """
    Receive and process GitHub webhook deliveries.

    1. Verify X-Hub-Signature-256 using the shared webhook secret.
    2. Route by X-GitHub-Event header (ping, pull_request, or ignore).
    3. Parse JSON body (rejecting malformed JSON with 400).
    4. Validate PR action (ignoring unhandled actions with 200).
    5. Extract and validate required PR identifiers (rejecting invalid/missing with 400).
    6. Schedule background PR context retrieval via BackgroundTasks.
    7. Return immediate HTTP 200 response with status 'accepted'.
    """
    if background_tasks is None:
        background_tasks = BackgroundTasks()

    # ---- Step 1: Read raw body and signature ----
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(body, signature, GITHUB_WEBHOOK_SECRET):
        logger.warning("Webhook rejected: invalid or missing signature")
        return Response(
            content="Invalid signature",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # ---- Step 2: Identify the event type ----
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

    logger.info(
        "Webhook received  event=%s  delivery=%s",
        event_type,
        delivery_id,
    )

    if event_type == "ping":
        logger.info("Ping event received — webhook is active")
        return {"status": "pong"}

    if event_type != "pull_request":
        logger.info("Event '%s' acknowledged but not processed", event_type)
        return {"status": "ignored", "reason": f"event '{event_type}' not handled"}

    # ---- Step 3: Parse JSON payload ----
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Webhook rejected: malformed JSON payload")
        return Response(
            content='{"status": "error", "reason": "malformed JSON payload"}',
            media_type="application/json",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not isinstance(payload, dict):
        logger.warning("Webhook rejected: JSON payload is not an object")
        return Response(
            content='{"status": "error", "reason": "malformed JSON payload"}',
            media_type="application/json",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ---- Step 4: Validate PR action ----
    action = payload.get("action", "unknown")
    if action not in HANDLED_PR_ACTIONS:
        logger.info(
            "PR event ignored  action=%s  (not in handled actions)",
            action,
        )
        return {"status": "ignored", "reason": f"action '{action}' not handled"}

    # ---- Step 5: Extract & validate PR identifiers ----
    pr_info = extract_pr_info(payload)
    if pr_info is None:
        logger.warning(
            "Webhook rejected: missing or invalid required PR fields (action=%s)",
            action,
        )
        return Response(
            content='{"status": "error", "reason": "missing or invalid required PR fields"}',
            media_type="application/json",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    logger.info(
        "PR event accepted  action=%s  repo=%s  pr=#%d  author=%s  %s -> %s",
        pr_info["action"],
        pr_info["repo_full_name"],
        pr_info["pull_number"],
        pr_info["pr_author"],
        pr_info["source_branch"],
        pr_info["target_branch"],
    )

    # ---- Step 6: Schedule background retrieval ----
    background_tasks.add_task(process_pull_request_context, pr_info)

    # ---- Step 7: Return immediate acknowledgement ----
    return {
        "status": "accepted",
        "pr_number": pr_info["pull_number"],
    }
