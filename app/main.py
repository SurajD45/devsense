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

import logging
import sys

from fastapi import FastAPI, Request, Response, status

from app.integrations.github.webhook import verify_signature, extract_pr_info
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


@app.post("/webhooks/github", status_code=status.HTTP_200_OK)
async def github_webhook(request: Request):
    """
    Receive and process GitHub webhook deliveries.

    1. Verify X-Hub-Signature-256 using the shared webhook secret.
    2. Route by X-GitHub-Event header.
    3. For pull_request events, log safe metadata for handled actions.
    """
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

    # ---- Step 3: Handle pull_request events ----
    if event_type == "pull_request":
        payload = await request.json()
        pr_info = extract_pr_info(payload)

        if pr_info is None:
            action = payload.get("action", "unknown")
            logger.info(
                "PR event ignored  action=%s  (not in handled actions)",
                action,
            )
            return {"status": "ignored", "reason": f"action '{action}' not handled"}

        logger.info(
            "PR event processed  action=%s  repo=%s  pr=#%s  author=%s  "
            "%s -> %s",
            pr_info["action"],
            pr_info["repo_full_name"],
            pr_info["pr_number"],
            pr_info["pr_author"],
            pr_info["source_branch"],
            pr_info["target_branch"],
        )

        return {
            "status": "processed",
            "event": "pull_request",
            "action": pr_info["action"],
            "repo": pr_info["repo_full_name"],
            "pr_number": pr_info["pr_number"],
        }

    # ---- Step 4: Acknowledge other events without processing ----
    if event_type == "ping":
        logger.info("Ping event received — webhook is active")
        return {"status": "pong"}

    logger.info("Event '%s' acknowledged but not processed", event_type)
    return {"status": "ignored", "reason": f"event '{event_type}' not handled"}
