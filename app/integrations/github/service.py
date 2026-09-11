"""
DevSense — GitHub Integration Service
======================================
Authentication service and client factory for the GitHub integration.
Binds configuration settings, JWT generation, and installation token
exchange to instantiate authenticated GitHubClient instances.

Public API:
    get_installation_client(installation_id: int) -> GitHubClient
"""

import logging

from app.integrations.github.auth import (
    GITHUB_API_BASE,
    create_jwt,
    create_installation_token,
)
from app.integrations.github.client import GitHubClient
from app.settings import GITHUB_APP_ID, GITHUB_PRIVATE_KEY_PATH

logger = logging.getLogger("github_service")


def get_installation_client(
    installation_id: int,
    *,
    app_id: str | None = None,
    private_key_path: str | None = None,
    base_url: str = GITHUB_API_BASE,
    timeout: int = 30,
) -> GitHubClient:
    """
    Create an authenticated GitHubClient instance for a GitHub App installation.

    Encapsulates the complete authentication boundary:
    1. Validates the installation_id.
    2. Loads GitHub App credentials (defaulting to app.settings).
    3. Generates a signed RS256 JWT using the App's private key.
    4. Exchanges the JWT for a short-lived installation access token.
    5. Returns an authenticated GitHubClient configured with that token.

    Args:
        installation_id: The numeric GitHub App installation ID.
        app_id: Optional GitHub App ID (defaults to GITHUB_APP_ID in settings).
        private_key_path: Optional path to RSA private key (defaults to settings).
        base_url: Optional GitHub API base URL (defaults to https://api.github.com).
        timeout: Optional HTTP request timeout in seconds (defaults to 30).

    Returns:
        An authenticated GitHubClient instance ready for API operations.

    Raises:
        ValueError: If installation_id, app_id, or private_key_path is invalid.
        FileNotFoundError: If the private key file does not exist.
        requests.HTTPError: If token exchange fails with the GitHub API.
        GitHubClientError: If client initialization fails.
    """
    if isinstance(installation_id, bool) or not isinstance(installation_id, int) or installation_id <= 0:
        raise ValueError("installation_id must be a positive integer")

    effective_app_id = app_id if app_id is not None else GITHUB_APP_ID
    if not effective_app_id or not isinstance(effective_app_id, str) or not effective_app_id.strip():
        raise ValueError("GITHUB_APP_ID is not configured or invalid")

    effective_key_path = private_key_path if private_key_path is not None else GITHUB_PRIVATE_KEY_PATH
    if not effective_key_path or not isinstance(effective_key_path, str) or not effective_key_path.strip():
        raise ValueError("GITHUB_PRIVATE_KEY_PATH is not configured or invalid")

    # Step 1: Generate App JWT (RS256)
    jwt_token = create_jwt(effective_app_id.strip(), effective_key_path.strip())

    # Step 2: Exchange JWT for installation access token
    installation_token = create_installation_token(jwt_token, installation_id)

    # Step 3: Instantiate and return authenticated GitHubClient
    client = GitHubClient(
        installation_token,
        base_url=base_url,
        timeout=timeout,
    )

    logger.info("Created GitHub client for installation ID: %d", installation_id)
    return client
