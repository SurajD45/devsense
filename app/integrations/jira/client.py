"""Jira REST API client for the DevSense V1 Jira foundation."""

from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth

from app.integrations.jira.models import JiraIssue
from app.integrations.jira.parser import JiraResponseError, parse_issue
from app.settings import JIRA_API_TOKEN, JIRA_BASE_URL, JIRA_EMAIL


class JiraClientError(RuntimeError):
    """Base error for Jira integration failures."""


class JiraConfigurationError(JiraClientError):
    """Raised when required Jira configuration is missing."""


class JiraAuthenticationError(JiraClientError):
    """Raised when Jira rejects authentication or access."""


class JiraIssueNotFoundError(JiraClientError):
    """Raised when the requested Jira issue does not exist or is inaccessible."""


class JiraNetworkError(JiraClientError):
    """Raised when the Jira API cannot be reached."""


class JiraClient:
    """Small read-only Jira client for retrieving one issue by key."""

    def __init__(
        self,
        base_url: str = JIRA_BASE_URL,
        email: str = JIRA_EMAIL,
        api_token: str = JIRA_API_TOKEN,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.timeout = timeout
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        missing = []
        if not self.base_url:
            missing.append("JIRA_BASE_URL")
        if not self.email:
            missing.append("JIRA_EMAIL")
        if not self.api_token:
            missing.append("JIRA_API_TOKEN")
        if missing:
            raise JiraConfigurationError(
                "Missing required Jira configuration: " + ", ".join(missing)
            )

    def get_issue(self, issue_key: str) -> JiraIssue:
        """Retrieve and normalize one Jira issue by its issue key."""
        if not isinstance(issue_key, str) or not issue_key.strip():
            raise ValueError("issue_key must be a non-empty string")

        encoded_key = quote(issue_key.strip(), safe="")
        url = f"{self.base_url}/rest/api/3/issue/{encoded_key}"

        try:
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.email, self.api_token),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise JiraNetworkError("Unable to connect to Jira") from exc

        if response.status_code in (401, 403):
            raise JiraAuthenticationError(
                f"Jira authentication/access failed (HTTP {response.status_code})"
            )
        if response.status_code == 404:
            raise JiraIssueNotFoundError(f"Jira issue not found: {issue_key.strip()}")
        if not 200 <= response.status_code < 300:
            raise JiraClientError(
                f"Jira API request failed (HTTP {response.status_code})"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise JiraResponseError("Jira returned invalid JSON") from exc

        try:
            return parse_issue(payload)
        except JiraResponseError as exc:
            raise JiraClientError("Jira returned an unexpected issue response") from exc
