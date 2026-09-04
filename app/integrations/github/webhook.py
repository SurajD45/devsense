"""
GitHub Webhook Signature Verification & Event Handling
=======================================================
Provides HMAC-SHA256 signature verification for incoming GitHub webhooks
and extraction of pull request event data.

Security:
  - The webhook secret is loaded from the GITHUB_WEBHOOK_SECRET env var.
  - Signatures, secrets, and tokens are never logged.
  - Only safe PR metadata is logged (repo name, PR number, branches, author).
"""

import hashlib
import hmac
import logging

logger = logging.getLogger("webhook")


# ---------------------------------------------------------------------------
# Signature Verification
# ---------------------------------------------------------------------------

def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header against the raw request body.

    GitHub sends: sha256=<hex-digest>
    We compute:   HMAC-SHA256(secret, payload_body) and compare in constant time.

    Args:
        payload_body:     The raw bytes of the HTTP request body.
        signature_header: The value of the X-Hub-Signature-256 header.
        secret:           The webhook secret (from environment).

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature_header:
        return False

    # GitHub format: "sha256=<hex>"
    if not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header[len("sha256="):]

    computed = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_signature)


# ---------------------------------------------------------------------------
# PR Event Extraction
# ---------------------------------------------------------------------------

# Actions we care about for pull_request events.
HANDLED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def extract_pr_info(payload: dict) -> dict | None:
    """
    Extract safe, loggable metadata from a pull_request webhook payload.

    Returns None if the action is not one we handle.

    Args:
        payload: The parsed JSON body of the webhook request.

    Returns:
        A dict with safe PR metadata, or None if the action is ignored.
    """
    action = payload.get("action", "")

    if action not in HANDLED_PR_ACTIONS:
        return None

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    return {
        "action": action,
        "repo_full_name": repo.get("full_name", "unknown"),
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url", ""),
        "pr_author": pr.get("user", {}).get("login", "unknown"),
        "source_branch": pr.get("head", {}).get("ref", "unknown"),
        "target_branch": pr.get("base", {}).get("ref", "unknown"),
    }
