"""
GitHub API Client Module
========================
Communicates with the GitHub REST API using an installation access token
to retrieve Pull Request metadata for DevSense.

Security:
  - Access tokens are never logged or exposed in exception messages.
  - Returns only curated, non-sensitive metadata needed for analysis.
"""

import logging
import requests

from app.integrations.github.auth import (
    GITHUB_API_BASE,
    GITHUB_API_VERSION,
)

logger = logging.getLogger("github_client")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GitHubClientError(Exception):
    """Base exception for all GitHub client errors."""


class GitHubAPIError(GitHubClientError):
    """Raised when the GitHub API returns an HTTP error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubNotFoundError(GitHubAPIError):
    """Raised when a repository or PR does not exist or is inaccessible (HTTP 404)."""

    def __init__(self, message: str = "Repository or Pull Request not found") -> None:
        super().__init__(message, status_code=404)


class GitHubAuthenticationError(GitHubAPIError):
    """Raised when authentication fails (HTTP 401)."""

    def __init__(self, message: str = "GitHub API authentication failed") -> None:
        super().__init__(message, status_code=401)


class GitHubPermissionError(GitHubAPIError):
    """Raised when API access is forbidden or rate limited (HTTP 403)."""

    def __init__(self, message: str = "GitHub API permission denied") -> None:
        super().__init__(message, status_code=403)


class GitHubNetworkError(GitHubClientError):
    """Raised when a network or connection error occurs."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GitHubClient:
    """
    GitHub REST API client for DevSense.

    Communicates with the GitHub API using an installation access token
    to fetch pull request metadata.
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = GITHUB_API_BASE,
        timeout: int = 30,
    ) -> None:
        """
        Initialize the GitHub client with an installation access token.

        Args:
            access_token: A valid GitHub installation access token.
            base_url: Base URL for GitHub API (defaults to https://api.github.com).
            timeout: Request timeout in seconds (defaults to 30).

        Raises:
            ValueError: If access_token is empty/not a string, or if timeout is not a positive number.
        """
        if not access_token or not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("access_token must be a non-empty string")

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number")

        self._access_token = access_token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict:
        """Construct standard GitHub API request headers."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    def get_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict:
        """
        Retrieve metadata for a specific Pull Request.

        Args:
            owner: Repository owner (user or organization).
            repo: Repository name.
            pull_number: Pull request number (positive integer).

        Returns:
            Dict containing curated PR metadata with keys:
            - number (int)
            - title (str)
            - body (str)
            - state (str)
            - author (str)
            - html_url (str)
            - base_branch (str)
            - head_branch (str)
            - created_at (str)
            - updated_at (str)

        Raises:
            ValueError: If parameters are invalid.
            GitHubNotFoundError: If the repository or PR does not exist (HTTP 404).
            GitHubAuthenticationError: If the access token is invalid or expired (HTTP 401).
            GitHubPermissionError: If access is forbidden or rate-limited (HTTP 403).
            GitHubAPIError: For other GitHub API HTTP errors.
            GitHubNetworkError: For network timeouts or connection failures.
        """
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if not repo or not isinstance(repo, str) or not repo.strip():
            raise ValueError("repo must be a non-empty string")
        if not isinstance(pull_number, int) or pull_number <= 0:
            raise ValueError("pull_number must be a positive integer")

        owner_clean = owner.strip()
        repo_clean = repo.strip()

        url = f"{self._base_url}/repos/{owner_clean}/{repo_clean}/pulls/{pull_number}"
        headers = self._headers()

        try:
            response = requests.get(url, headers=headers, timeout=self._timeout)
        except requests.exceptions.Timeout as exc:
            logger.error(
                "GitHub API timeout while fetching PR #%d for %s/%s",
                pull_number,
                owner_clean,
                repo_clean,
            )
            raise GitHubNetworkError(
                f"Request timed out while fetching PR #{pull_number} for {owner_clean}/{repo_clean}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.error(
                "GitHub API network failure while fetching PR #%d for %s/%s: %s",
                pull_number,
                owner_clean,
                repo_clean,
                type(exc).__name__,
            )
            raise GitHubNetworkError(
                f"Network error while communicating with GitHub API: {type(exc).__name__}"
            ) from exc

        if response.status_code == 200:
            data = response.json()
            return self._extract_pr_metadata(data)

        if response.status_code == 404:
            logger.warning(
                "PR #%d not found in %s/%s (HTTP 404)",
                pull_number,
                owner_clean,
                repo_clean,
            )
            raise GitHubNotFoundError(
                f"Pull request #{pull_number} not found in {owner_clean}/{repo_clean}"
            )

        if response.status_code == 401:
            logger.error("GitHub API authentication failed (HTTP 401)")
            raise GitHubAuthenticationError(
                "GitHub API authentication failed. Verify that the installation token is valid."
            )

        if response.status_code == 403:
            logger.error(
                "GitHub API access forbidden for %s/%s (HTTP 403)",
                owner_clean,
                repo_clean,
            )
            raise GitHubPermissionError(
                f"GitHub API permission denied for {owner_clean}/{repo_clean}. Check App permissions or rate limits."
            )

        logger.error(
            "GitHub API returned error HTTP %d for %s/%s PR #%d",
            response.status_code,
            owner_clean,
            repo_clean,
            pull_number,
        )
        raise GitHubAPIError(
            f"GitHub API error (HTTP {response.status_code}) for {owner_clean}/{repo_clean} PR #{pull_number}",
            status_code=response.status_code,
        )

    @staticmethod
    def _extract_pr_metadata(data: dict) -> dict:
        """Extract and sanitize only the useful PR metadata fields required by DevSense."""
        user = data.get("user") or {}
        author = user.get("login", "")

        base = data.get("base") or {}
        base_branch = base.get("ref", "")

        head = data.get("head") or {}
        head_branch = head.get("ref", "")

        raw_body = data.get("body")
        body = "" if raw_body is None else raw_body

        return {
            "number": data.get("number"),
            "title": data.get("title", ""),
            "body": body,
            "state": data.get("state", ""),
            "author": author,
            "html_url": data.get("html_url", ""),
            "base_branch": base_branch,
            "head_branch": head_branch,
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        }
