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
import re
import sys

from fastapi import BackgroundTasks, FastAPI, Request, Response, status

from pydantic import ValidationError

from app.domain.models import InvestigationStatus, PullRequest
from app.integrations.github.client import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)
from app.integrations.github.content import (
    ChangedFileContentBatch,
    FileContentStatus,
    GitHubFileContentService,
)
from app.integrations.github.mapper import GitHubPullRequestMapper
from app.integrations.github.service import get_installation_client
from app.integrations.github.webhook import (
    HANDLED_PR_ACTIONS,
    extract_pr_info,
    verify_signature,
)
from app.integrations.jira.bridge import BridgeResult, GitHubJiraBridge
from app.investigation.pipeline import InvestigationPipelineOrchestrator
from app.investigation.schemas import (
    InvestigationPipelineInput,
    InvestigationResultBundle,
)
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


_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _clean_sha(value: object) -> str | None:
    """Validate that value is a 7-40 char hexadecimal SHA string, normalized to lowercase."""
    if isinstance(value, str):
        cleaned = value.strip()
        if _HEX_SHA_PATTERN.fullmatch(cleaned):
            return cleaned.lower()
    return None


class ProcessedPRContext(dict):
    """
    Result of PR context retrieval, mapping, Jira requirement resolution,
    changed-file content retrieval, and deterministic pipeline orchestration.

    Inherits from dict for full backward compatibility with callers and tests expecting
    the raw GitHub context dictionary, while exposing the mapped PullRequest,
    BridgeResult models, head_commit_sha, file contents, investigation bundle,
    and diagnostics via attributes and the raw_context property.

    Design Decision:
      The underlying dictionary contains strictly the original raw GitHub context fields,
      preserving raw payload dictionary equality (context == raw_context) and JSON
      serialization compatibility (json.dumps(context)). Mapped domain models (PullRequest,
      BridgeResult), retrieved file contents, and pipeline investigation artifacts are
      exposed explicitly as attributes and properties, preventing domain models or large
      file buffers from contaminating raw dictionary keys or breaking JSON serializers.
    """

    def __init__(
        self,
        raw_context: dict,
        pull_request: PullRequest | None = None,
        bridge_result: BridgeResult | None = None,
        head_commit_sha: str | None = None,
        file_contents: dict[str, str] | None = None,
        content_batch: ChangedFileContentBatch | None = None,
        investigation_bundle: InvestigationResultBundle | None = None,
        investigation_error: str | None = None,
    ) -> None:
        super().__init__(raw_context)
        self.pull_request = pull_request
        self.bridge_result = bridge_result
        self.head_commit_sha = head_commit_sha
        self.file_contents = file_contents if file_contents is not None else {}
        self.content_batch = content_batch
        self.investigation_bundle = investigation_bundle
        self.investigation_error = investigation_error

    @property
    def raw_context(self) -> dict:
        """Returns the original raw GitHub context dictionary."""
        return dict(self)



def process_pull_request_context(
    pr_info: dict,
    mapper: GitHubPullRequestMapper | None = None,
    bridge: GitHubJiraBridge | None = None,
    content_service: GitHubFileContentService | None = None,
    orchestrator: InvestigationPipelineOrchestrator | None = None,
) -> ProcessedPRContext | dict | None:
    """
    Retrieve and process PR context asynchronously in a background task.

    1. Obtains authenticated GitHubClient via get_installation_client(installation_id).
    2. Retrieves raw PR context (metadata, changed files with patches, commits).
    3. Maps raw context to canonical PullRequest domain model via GitHubPullRequestMapper.
    4. Resolves linked Jira Requirement via GitHubJiraBridge using PR title and description.
    5. Resolves verified PR head commit SHA from live GitHub PR metadata.
    6. Retrieves changed-file contents pinned to the verified head commit SHA.
    7. Executes deterministic InvestigationPipelineOrchestrator if prerequisites are satisfied.
    8. Returns ProcessedPRContext bundling raw context, PullRequest, BridgeResult,
       file contents, investigation bundle, and diagnostics.

    Security & Reliability:
      - Traps all authentication, API, network, validation, and unexpected errors without crashing.
      - Never logs tokens, JWTs, keys, patches, or credentials.
      - Gracefully handles PRs with no Jira key, missing head SHA, or unavailable Jira API.
      - Runs orchestration only on pinned live head commit content.

    Args:
        pr_info: Dictionary containing validated PR identifiers:
                 'installation_id', 'owner', 'repo', 'pull_number'.
        mapper: Optional GitHubPullRequestMapper override (defaults to new instance).
        bridge: Optional GitHubJiraBridge override (defaults to get_jira_bridge()).
        content_service: Optional GitHubFileContentService override.
        orchestrator: Optional InvestigationPipelineOrchestrator override.

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

        # Step 4: Resolve verified PR head commit SHA from authoritative live GitHub PR metadata
        # Invariant: Head SHA must come from authoritative live GitHub PR head metadata (atomically consistent
        # with context["files"]). Webhook payload SHA is NEVER used for content retrieval, guaranteeing snapshot consistency.
        # We never infer head SHA from commits[-1].sha, branch names, base branch, or webhook fallback.
        live_api_sha = (
            _clean_sha(pull_request.head_sha if pull_request else None)
            or _clean_sha(context.get("pull_request", {}).get("head_sha"))
            or _clean_sha(
                context.get("pull_request", {}).get("head", {}).get("sha")
                if isinstance(context.get("pull_request", {}).get("head"), dict)
                else None
            )
        )
        webhook_sha = _clean_sha(pr_info.get("head_commit_sha"))

        verified_head_sha: str | None = None
        if live_api_sha:
            verified_head_sha = live_api_sha
            if webhook_sha and webhook_sha != live_api_sha:
                logger.warning(
                    "PR head discrepancy for %s/%s PR #%d: webhook head SHA (%s) differs from live GitHub head SHA (%s); selected live GitHub head.",
                    owner,
                    repo,
                    pull_number,
                    webhook_sha[:7],
                    live_api_sha[:7],
                )

        if pull_request is not None and pull_request.head_sha is None and verified_head_sha:
            pull_request.head_sha = verified_head_sha

        # Step 5: Retrieve changed-file contents pinned to the exact verified head commit SHA
        file_contents: dict[str, str] = {}
        content_batch: ChangedFileContentBatch | None = None

        if not verified_head_sha:
            if webhook_sha:
                logger.warning(
                    "Live GitHub PR head SHA is unavailable for %s/%s PR #%d despite valid webhook SHA (%s); "
                    "skipping changed-file content retrieval to guarantee snapshot consistency.",
                    owner,
                    repo,
                    pull_number,
                    webhook_sha[:7],
                )
            else:
                logger.warning(
                    "Authoritative PR head commit SHA is unavailable for %s/%s PR #%d; "
                    "failing closed and skipping changed-file content retrieval to prevent unpinned reads.",
                    owner,
                    repo,
                    pull_number,
                )
        else:
            try:
                file_service = content_service or GitHubFileContentService(client=client)
                files_to_retrieve = (
                    pull_request.changed_files
                    if pull_request is not None and pull_request.changed_files
                    else context.get("files", [])
                )
                content_batch = file_service.retrieve_contents(
                    repository=f"{owner}/{repo}",
                    head_commit_sha=verified_head_sha,
                    changed_files=files_to_retrieve,
                    client=client,
                )
                file_contents = content_batch.contents
                logger.info(
                    "Retrieved content for %d/%d changed files at head commit %s for %s/%s PR #%d (%d failures/skipped)",
                    len(file_contents),
                    len(files_to_retrieve),
                    verified_head_sha[:7],
                    owner,
                    repo,
                    pull_number,
                    len(content_batch.failures),
                )
            except Exception as exc:
                logger.error(
                    "Error retrieving changed-file contents for %s/%s PR #%d at %s: %s",
                    owner,
                    repo,
                    pull_number,
                    verified_head_sha[:7],
                    exc,
                )

        # Step 6: Execute investigation pipeline orchestration if eligible
        investigation_bundle: InvestigationResultBundle | None = None
        investigation_error: str | None = None

        if pull_request is None:
            investigation_error = (
                "Canonical PullRequest model could not be mapped from context; "
                "orchestration skipped."
            )
            logger.info(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif bridge_result is None or not bridge_result.has_requirement:
            reason = (
                bridge_result.reason
                if bridge_result and bridge_result.reason
                else "No Jira requirement resolved"
            )
            investigation_error = (
                f"Missing linked Jira requirement ({reason}); orchestration skipped."
            )
            logger.info(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif not bridge_result.requirement.acceptance_criteria:
            investigation_error = (
                "Linked Jira requirement contains no acceptance criteria; "
                "orchestration skipped."
            )
            logger.warning(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif not verified_head_sha:
            investigation_error = (
                "Authoritative live GitHub PR head SHA is unavailable; "
                "orchestration skipped."
            )
            logger.warning(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif not pull_request.changed_files:
            investigation_error = (
                "Pull request contains no changed files; orchestration skipped."
            )
            logger.info(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif content_batch is None:
            investigation_error = (
                "Changed-file content retrieval failed or is unavailable; "
                "orchestration skipped."
            )
            logger.warning(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        elif not file_contents:
            investigation_error = (
                "No usable changed-file content could be retrieved; "
                "orchestration skipped."
            )
            logger.warning(
                "Skipping investigation orchestration for %s/%s PR #%d: %s",
                owner,
                repo,
                pull_number,
                investigation_error,
            )
        else:
            # Check for eligible changed files that failed content retrieval
            results_dict = getattr(content_batch, "results", None)
            if isinstance(results_dict, dict):
                failed_eligible = {
                    path: (res.error or getattr(res.status, "value", str(res.status)))
                    for path, res in results_dict.items()
                    if getattr(res, "status", None)
                    not in (
                        FileContentStatus.SUCCESS,
                        FileContentStatus.DELETED,
                        FileContentStatus.BINARY,
                    )
                }
            else:
                failures_dict = getattr(content_batch, "failures", None)
                if isinstance(failures_dict, dict):
                    failed_eligible = dict(failures_dict)
                else:
                    failed_eligible = {}

            try:
                pipeline_input = InvestigationPipelineInput(
                    pull_request=pull_request,
                    requirement=bridge_result.requirement,
                    file_contents=file_contents,
                    test_results=[],
                )
                pipeline_orchestrator = orchestrator or InvestigationPipelineOrchestrator()
                bundle = pipeline_orchestrator.orchestrate(
                    pipeline_input,
                    head_commit_sha=verified_head_sha,
                )

                inv = getattr(bundle, "investigation", None)
                inv_status = getattr(inv, "status", None)
                if inv_status == InvestigationStatus.FAILED:
                    investigation_bundle = bundle
                    err_list = getattr(bundle, "errors", [])
                    investigation_error = (
                        "; ".join(err_list)
                        if err_list
                        else "Investigation pipeline returned FAILED status"
                    )
                    logger.warning(
                        "Investigation pipeline completed with FAILED status for %s/%s PR #%d: %s",
                        owner,
                        repo,
                        pull_number,
                        investigation_error,
                    )
                else:
                    if failed_eligible:
                        diagnostic = (
                            f"Incomplete file content retrieval: {len(failed_eligible)} eligible file(s) "
                            f"failed retrieval ({', '.join(sorted(failed_eligible.keys()))})"
                        )
                        logger.warning(
                            "Investigation completed on partial inputs for %s/%s PR #%d: %s",
                            owner,
                            repo,
                            pull_number,
                            diagnostic,
                        )
                        investigation_error = diagnostic
                        if type(bundle) is InvestigationResultBundle:
                            bundle = bundle.model_copy(
                                update={"errors": list(bundle.errors) + [diagnostic]}
                            )
                        elif hasattr(bundle, "errors") and isinstance(bundle.errors, list):
                            bundle.errors.append(diagnostic)
                    investigation_bundle = bundle
                    findings_count = len(getattr(bundle, "findings", []))
                    evidence_count = len(getattr(bundle, "evidence", []))
                    logger.info(
                        "Investigation pipeline completed for %s/%s PR #%d: %d findings, %d evidence",
                        owner,
                        repo,
                        pull_number,
                        findings_count,
                        evidence_count,
                    )
            except Exception as exc:
                logger.error(
                    "Investigation pipeline exception for %s/%s PR #%d: %s",
                    owner,
                    repo,
                    pull_number,
                    exc,
                    exc_info=True,
                )
                investigation_bundle = None
                investigation_error = (
                    f"Pipeline execution failure: {type(exc).__name__}: {str(exc)}"
                )

        # Step 7: Make resulting models, head SHA, file contents, and investigation results available
        return ProcessedPRContext(
            raw_context=context,
            pull_request=pull_request,
            bridge_result=bridge_result,
            head_commit_sha=verified_head_sha,
            file_contents=file_contents,
            content_batch=content_batch,
            investigation_bundle=investigation_bundle,
            investigation_error=investigation_error,
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
