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

# Safety cap for pagination to prevent infinite loops (up to 10,000 files)
DEFAULT_MAX_PAGES = 100


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

        self._handle_error_response(response, owner_clean, repo_clean, pull_number)

    def get_pull_request_files(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> list[dict]:
        """
        Retrieve the list of files changed by a specific Pull Request.

        Handles pagination automatically to retrieve all changed files across
        pages until the last page or max_pages safety limit.

        Args:
            owner: Repository owner (user or organization).
            repo: Repository name.
            pull_number: Pull request number (positive integer).
            max_pages: Maximum number of pages to fetch (safety termination limit,
                       defaults to 100).

        Returns:
            List of dicts, each containing:
            - filename (str)
            - status (str)
            - additions (int)
            - deletions (int)
            - changes (int)
            - patch (str, defaults to "" if missing or null)

        Raises:
            ValueError: If parameters are invalid.
            GitHubNotFoundError: If the repository or PR does not exist (HTTP 404).
            GitHubAuthenticationError: If the access token is invalid or expired (HTTP 401).
            GitHubPermissionError: If access is forbidden or rate-limited (HTTP 403).
            GitHubAPIError: For other GitHub API HTTP errors or if max_pages limit is reached.
            GitHubNetworkError: For network timeouts or connection failures.
        """
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")
        if not repo or not isinstance(repo, str) or not repo.strip():
            raise ValueError("repo must be a non-empty string")
        if not isinstance(pull_number, int) or pull_number <= 0:
            raise ValueError("pull_number must be a positive integer")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")

        owner_clean = owner.strip()
        repo_clean = repo.strip()

        url = f"{self._base_url}/repos/{owner_clean}/{repo_clean}/pulls/{pull_number}/files"
        headers = self._headers()

        all_files: list[dict] = []
        page = 1
        per_page = 100

        while True:
            if page > max_pages:
                logger.error(
                    "Exceeded pagination limit of %d pages for %s/%s PR #%d",
                    max_pages,
                    owner_clean,
                    repo_clean,
                    pull_number,
                )
                raise GitHubAPIError(
                    f"Exceeded maximum pagination limit ({max_pages} pages) while fetching files for {owner_clean}/{repo_clean} PR #{pull_number}"
                )

            params = {"per_page": per_page, "page": page}

            try:
                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                )
            except requests.exceptions.Timeout as exc:
                logger.error(
                    "GitHub API timeout while fetching PR #%d files for %s/%s (page %d)",
                    pull_number,
                    owner_clean,
                    repo_clean,
                    page,
                )
                raise GitHubNetworkError(
                    f"Request timed out while fetching PR #{pull_number} files for {owner_clean}/{repo_clean}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                logger.error(
                    "GitHub API network failure while fetching PR #%d files for %s/%s (page %d): %s",
                    pull_number,
                    owner_clean,
                    repo_clean,
                    page,
                    type(exc).__name__,
                )
                raise GitHubNetworkError(
                    f"Network error while communicating with GitHub API: {type(exc).__name__}"
                ) from exc

            if response.status_code != 200:
                self._handle_error_response(response, owner_clean, repo_clean, pull_number)

            data = response.json()
            if not isinstance(data, list):
                logger.error(
                    "Unexpected GitHub API response format for %s/%s PR #%d files (expected list, got %s)",
                    owner_clean,
                    repo_clean,
                    pull_number,
                    type(data).__name__,
                )
                raise GitHubAPIError(
                    f"Unexpected response format from GitHub API: expected list, got {type(data).__name__}"
                )

            if not data:
                break

            for item in data:
                all_files.append(self._extract_file_metadata(item))

            # Deterministic page-size pagination:
            # If fewer items than per_page were returned, this is the last page.
            if len(data) < per_page:
                break

            page += 1

        logger.info(
            "Retrieved %d changed files for %s/%s PR #%d across %d page(s)",
            len(all_files),
            owner_clean,
            repo_clean,
            pull_number,
            page,
        )
        return all_files

    def _handle_error_response(
        self,
        response: requests.Response,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> None:
        """Handle non-200 HTTP responses uniformly without exposing credentials."""
        if response.status_code == 404:
            logger.warning(
                "PR #%d not found in %s/%s (HTTP 404)",
                pull_number,
                owner,
                repo,
            )
            raise GitHubNotFoundError(
                f"Pull request #{pull_number} not found in {owner}/{repo}"
            )

        if response.status_code == 401:
            logger.error("GitHub API authentication failed (HTTP 401)")
            raise GitHubAuthenticationError(
                "GitHub API authentication failed. Verify that the installation token is valid."
            )

        if response.status_code == 403:
            logger.error(
                "GitHub API access forbidden for %s/%s (HTTP 403)",
                owner,
                repo,
            )
            raise GitHubPermissionError(
                f"GitHub API permission denied for {owner}/{repo}. Check App permissions or rate limits."
            )

        logger.error(
            "GitHub API returned error HTTP %d for %s/%s PR #%d",
            response.status_code,
            owner,
            repo,
            pull_number,
        )
        raise GitHubAPIError(
            f"GitHub API error (HTTP {response.status_code}) for {owner}/{repo} PR #{pull_number}",
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

    @staticmethod
    def _extract_file_metadata(data: dict) -> dict:
        """Extract and sanitize only the required changed file metadata."""
        raw_patch = data.get("patch")
        patch = "" if raw_patch is None else str(raw_patch)

        return {
            "filename": data.get("filename", ""),
            "status": data.get("status", ""),
            "additions": data.get("additions", 0),
            "deletions": data.get("deletions", 0),
            "changes": data.get("changes", 0),
            "patch": patch,
        }
