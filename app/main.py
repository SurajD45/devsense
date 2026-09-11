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

from app.integrations.github.client import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)
from app.integrations.github.service import get_installation_client
from app.integrations.github.webhook import (
    HANDLED_PR_ACTIONS,
    extract_pr_info,
    verify_signature,
)
from app.settings import GITHUB_WEBHOOK_SECRET

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


def process_pull_request_context(pr_info: dict) -> dict | None:
    """
    Retrieve and aggregate PR context asynchronously in a background task.

    1. Obtains authenticated GitHubClient via get_installation_client(installation_id).
    2. Retrieves PR context (metadata, changed files with patches, commits).
    3. Safely logs summary of evidence retrieved.
    4. Returns aggregated context for the future investigation pipeline.

    Security & Reliability:
      - Traps all authentication, API, network, and unexpected errors without crashing.
      - Never logs tokens, JWTs, keys, patches, or full bodies.

    Args:
        pr_info: Dictionary containing validated PR identifiers:
                 'installation_id', 'owner', 'repo', 'pull_number'.

    Returns:
        Aggregated context dictionary, or None if retrieval failed.
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
        return context

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
