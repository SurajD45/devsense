"""
DevSense — GitHub Integration Package
======================================
Provides authenticated GitHub API access, webhook handling, and PR context retrieval.

Public API:
    - get_installation_client: Factory for authenticated GitHubClient instances.
    - GitHubClient: Low-level REST API client.
    - GitHubPullRequestMapper: Transform PR context into validated domain models.
    - GitHubClientError, GitHubAPIError, GitHubNotFoundError, ...: Client exceptions.
"""

from app.integrations.github.client import (
    DEFAULT_MAX_PAGES,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)
from app.integrations.github.mapper import GitHubPullRequestMapper
from app.integrations.github.service import get_installation_client

__all__ = [
    "DEFAULT_MAX_PAGES",
    "GitHubClient",
    "GitHubClientError",
    "GitHubAPIError",
    "GitHubNotFoundError",
    "GitHubAuthenticationError",
    "GitHubPermissionError",
    "GitHubNetworkError",
    "GitHubPullRequestMapper",
    "get_installation_client",
]
