"""
GitHub App Authentication Module
=================================
Handles JWT generation, installation discovery, and access token creation
for the AI PR Investigator GitHub App.

Authentication flow:
  1. Generate a short-lived JWT signed with the App's RSA private key.
  2. Use the JWT to list installations and find the one matching the target account.
  3. Exchange the JWT for an installation access token scoped to that installation.

Security:
  - The private key is read from disk and held only in memory.
  - No secrets are ever printed, logged, or included in error messages.
"""

import os
import time

import jwt
import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
JWT_ALGORITHM = "RS256"
JWT_EXPIRY_SECONDS = 540          # 9 minutes (with margin for clock skew)
JWT_CLOCK_DRIFT_SECONDS = 60      # Issue JWT 60s in the past for clock skew


def _api_headers(token: str, *, token_type: str = "Bearer") -> dict:
    """Return standard GitHub API request headers."""
    return {
        "Authorization": f"{token_type} {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


# ---------------------------------------------------------------------------
# Step 1 — JWT Generation
# ---------------------------------------------------------------------------

def create_jwt(app_id: str, private_key_path: str) -> str:
    """
    Create a short-lived JWT for GitHub App authentication.

    Args:
        app_id:           The GitHub App ID (from environment).
        private_key_path: Filesystem path to the App's RSA private key (.pem).

    Returns:
        Encoded JWT string.

    Raises:
        FileNotFoundError: If the private key file does not exist.
        ValueError:        If the private key file is empty or unreadable.
    """
    # Validate the key path without revealing key contents in errors.
    if not os.path.isfile(private_key_path):
        raise FileNotFoundError(
            f"Private key file not found at: {private_key_path}"
        )

    private_key = _read_private_key(private_key_path)

    now = int(time.time())
    payload = {
        "iat": now - JWT_CLOCK_DRIFT_SECONDS,   # Issued-at (with drift buffer)
        "exp": now + JWT_EXPIRY_SECONDS,         # Expires-at (10 min max)
        "iss": app_id,                           # Issuer = App ID
    }

    return jwt.encode(payload, private_key, algorithm=JWT_ALGORITHM)


def _read_private_key(path: str) -> str:
    """Read the RSA private key from *path*. Never log its contents."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read()
    except OSError:
        raise ValueError(
            f"Could not read private key file at: {path}"
        )

    if not key.strip():
        raise ValueError(
            f"Private key file is empty: {path}"
        )

    return key


# ---------------------------------------------------------------------------
# Step 2 — Installation Discovery
# ---------------------------------------------------------------------------

def get_installation_id(jwt_token: str, owner: str) -> int:
    """
    Find the installation ID for the GitHub account *owner*.

    Args:
        jwt_token: A valid GitHub App JWT.
        owner:     The GitHub username or organisation that installed the App.

    Returns:
        The numeric installation ID.

    Raises:
        RuntimeError: If no installation is found for the given owner.
        requests.HTTPError: If the GitHub API request fails.
    """
    url = f"{GITHUB_API_BASE}/app/installations"
    response = requests.get(url, headers=_api_headers(jwt_token), timeout=30)
    response.raise_for_status()

    installations = response.json()

    for installation in installations:
        account_login = installation.get("account", {}).get("login", "")
        if account_login.lower() == owner.lower():
            return installation["id"]

    raise RuntimeError(
        f"No installation found for account '{owner}'. "
        f"Ensure the GitHub App is installed on that account."
    )


# ---------------------------------------------------------------------------
# Step 3 — Installation Access Token
# ---------------------------------------------------------------------------

def create_installation_token(jwt_token: str, installation_id: int) -> str:
    """
    Exchange a JWT for a short-lived installation access token.

    Args:
        jwt_token:       A valid GitHub App JWT.
        installation_id: The target installation's numeric ID.

    Returns:
        The installation access token string.

    Raises:
        requests.HTTPError: If the GitHub API request fails.
    """
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    response = requests.post(url, headers=_api_headers(jwt_token), timeout=30)
    response.raise_for_status()

    return response.json()["token"]


# ---------------------------------------------------------------------------
# Step 4 — Repository Access Verification
# ---------------------------------------------------------------------------

def verify_repository_access(token: str, owner: str, repo: str) -> dict:
    """
    Make a read-only API call to verify the token can access *owner/repo*.

    Args:
        token: A valid installation access token.
        owner: Repository owner (user or org).
        repo:  Repository name.

    Returns:
        A dict with safe, non-secret repository metadata:
        ``full_name``, ``owner``, ``visibility``, ``default_branch``.

    Raises:
        requests.HTTPError: If the repository is not accessible.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    response = requests.get(url, headers=_api_headers(token), timeout=30)
    response.raise_for_status()

    data = response.json()

    return {
        "full_name": data.get("full_name"),
        "owner": data.get("owner", {}).get("login"),
        "visibility": "private" if data.get("private") else "public",
        "default_branch": data.get("default_branch"),
    }
