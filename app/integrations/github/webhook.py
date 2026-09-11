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
    Extract safe metadata and required investigation identifiers from a
    pull_request webhook payload.

    Required fields:
      - installation_id: positive integer
      - owner: non-empty string
      - repo: non-empty string
      - pull_number: positive integer

    Returns None if:
      - The payload is not a dictionary.
      - The action is not in HANDLED_PR_ACTIONS.
      - Any required field is missing, null, or invalid.

    Args:
        payload: The parsed JSON body of the webhook request.

    Returns:
        A dict with validated PR identifiers and safe metadata, or None.
    """
    if not isinstance(payload, dict):
        return None

    action = payload.get("action", "")
    if action not in HANDLED_PR_ACTIONS:
        return None

    # Safely extract nested structures
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return None

    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return None

    installation = payload.get("installation")
    if not isinstance(installation, dict):
        return None

    # 1. Validate installation_id
    installation_id = installation.get("id")
    if isinstance(installation_id, bool) or not isinstance(installation_id, int) or installation_id <= 0:
        return None

    # 2. Validate pull_number
    pull_number = pr.get("number")
    if isinstance(pull_number, bool) or not isinstance(pull_number, int) or pull_number <= 0:
        return None

    # 3. Validate repo name & owner
    repo_name = repo.get("name")
    owner_data = repo.get("owner")
    owner_login = owner_data.get("login") if isinstance(owner_data, dict) else None

    # Fallback to repo.full_name if owner or name not explicitly provided
    if not owner_login or not repo_name:
        full_name = repo.get("full_name")
        if isinstance(full_name, str) and "/" in full_name:
            parts = full_name.split("/", 1)
            owner_login = owner_login or parts[0]
            repo_name = repo_name or parts[1]

    if not isinstance(owner_login, str) or not owner_login.strip():
        return None

    if not isinstance(repo_name, str) or not repo_name.strip():
        return None

    owner_clean = owner_login.strip()
    repo_clean = repo_name.strip()
    repo_full_name = repo.get("full_name") or f"{owner_clean}/{repo_clean}"

    # Extract additional metadata safely
    user = pr.get("user")
    pr_author = user.get("login", "unknown") if isinstance(user, dict) else "unknown"

    head = pr.get("head")
    source_branch = head.get("ref", "unknown") if isinstance(head, dict) else "unknown"

    base = pr.get("base")
    target_branch = base.get("ref", "unknown") if isinstance(base, dict) else "unknown"

    return {
        "action": action,
        "installation_id": installation_id,
        "owner": owner_clean,
        "repo": repo_clean,
        "pull_number": pull_number,
        "repo_full_name": repo_full_name,
        "pr_author": pr_author,
        "source_branch": source_branch,
        "target_branch": target_branch,
    }
