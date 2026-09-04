#!/usr/bin/env python3
"""
AI PR Investigator — GitHub App Authentication Test
=====================================================
A one-shot connectivity test that proves our GitHub App can:

  1. Generate a valid JWT.
  2. Discover its installation on the target account.
  3. Obtain an installation access token.
  4. Read from the target repository.

Usage:
    python -m scripts.github_app_test

Required environment variables (set in a .env file or shell):
    GITHUB_APP_ID            — The GitHub App's numeric ID.
    GITHUB_PRIVATE_KEY_PATH  — Path to the App's RSA private key (.pem).
    GITHUB_REPOSITORY_OWNER  — Owner (user/org) of the target repository.
    GITHUB_REPOSITORY_NAME   — Name of the target repository.
"""

import sys

from requests.exceptions import HTTPError

from app.integrations.github.auth import (
    create_jwt,
    get_installation_id,
    create_installation_token,
    verify_repository_access,
)
from app.settings import (
    GITHUB_APP_ID,
    GITHUB_PRIVATE_KEY_PATH,
    GITHUB_REPOSITORY_OWNER,
    GITHUB_REPOSITORY_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEPARATOR = "=" * 50


def _print_header() -> None:
    print()
    print(SEPARATOR)
    print("  AI PR Investigator — GitHub App Auth Test")
    print(SEPARATOR)


def _print_footer(success: bool) -> None:
    print(SEPARATOR)
    if success:
        print("  ALL CHECKS PASSED")
    else:
        print("  AUTHENTICATION FAILED")
    print(SEPARATOR)
    print()


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _print_header()

    # ---- Validate configuration from app.settings ----
    app_id = GITHUB_APP_ID
    private_key_path = GITHUB_PRIVATE_KEY_PATH
    repo_owner = GITHUB_REPOSITORY_OWNER
    repo_name = GITHUB_REPOSITORY_NAME

    for name, value in [
        ("GITHUB_APP_ID", app_id),
        ("GITHUB_PRIVATE_KEY_PATH", private_key_path),
        ("GITHUB_REPOSITORY_OWNER", repo_owner),
        ("GITHUB_REPOSITORY_NAME", repo_name),
    ]:
        if not value:
            print(f"[✗] Missing required environment variable: {name}")
            print(f"    Set it in your .env file or export it in your shell.")
            sys.exit(1)

    success = False

    try:
        # Step 1 — Generate JWT
        jwt_token = create_jwt(app_id, private_key_path)
        print("[✓] JWT generated successfully")

        # Step 2 — Discover installation
        installation_id = get_installation_id(jwt_token, repo_owner)
        print(f"[✓] Installation found (Installation ID: {installation_id})")

        # Step 3 — Obtain installation access token
        token = create_installation_token(jwt_token, installation_id)
        print("[✓] Installation access token obtained")

        # Step 4 — Verify repository access
        repo_info = verify_repository_access(token, repo_owner, repo_name)
        print("[✓] Repository access verified")
        print()
        print(f"    Repository : {repo_info['full_name']}")
        print(f"    Owner      : {repo_info['owner']}")
        print(f"    Visibility : {repo_info['visibility']}")
        print(f"    Branch     : {repo_info['default_branch']}")
        print()

        success = True

    except FileNotFoundError as exc:
        print(f"[✗] {exc}")
    except ValueError as exc:
        print(f"[✗] {exc}")
    except RuntimeError as exc:
        print(f"[✗] {exc}")
    except HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "N/A"
        print(f"[✗] GitHub API error (HTTP {status})")
        # Print the API error message but never raw credentials/tokens.
        if exc.response is not None:
            try:
                error_body = exc.response.json()
                message = error_body.get("message", "No details available.")
                print(f"    Message: {message}")
            except Exception:
                print(f"    Could not parse error response.")
    except Exception as exc:
        # Catch-all: show type and message, never secrets.
        print(f"[✗] Unexpected error: {type(exc).__name__}: {exc}")

    _print_footer(success)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
