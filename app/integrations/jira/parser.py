"""Normalize Jira REST API responses into DevSense internal models."""

from typing import Any

from app.integrations.jira.models import JiraIssue


class JiraResponseError(ValueError):
    """Raised when Jira returns an unexpected response shape."""


def _extract_adf_text(value: Any) -> str:
    """Extract readable text from Jira's Atlassian Document Format."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""

    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                parts.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return "\n".join(parts).strip()


def parse_issue(payload: Any) -> JiraIssue:
    """Convert a Jira issue JSON object into a normalized JiraIssue."""
    if not isinstance(payload, dict):
        raise JiraResponseError("Jira response must be a JSON object")

    key = payload.get("key")
    fields = payload.get("fields")
    if not isinstance(key, str) or not key.strip():
        raise JiraResponseError("Jira response is missing issue key")
    if not isinstance(fields, dict):
        raise JiraResponseError("Jira response is missing fields")

    summary = fields.get("summary")
    if not isinstance(summary, str):
        raise JiraResponseError("Jira response is missing a valid summary")

    status_value = fields.get("status")
    status = None
    if isinstance(status_value, dict):
        status_name = status_value.get("name")
        if status_name is not None and not isinstance(status_name, str):
            raise JiraResponseError("Jira response contains an invalid status")
        status = status_name

    issue_type_value = fields.get("issuetype")
    issue_type = None
    if isinstance(issue_type_value, dict):
        issue_type_name = issue_type_value.get("name")
        if issue_type_name is not None and not isinstance(issue_type_name, str):
            raise JiraResponseError("Jira response contains an invalid issue type")
        issue_type = issue_type_name

    description = _extract_adf_text(fields.get("description"))
    return JiraIssue(
        key=key.strip(),
        summary=summary.strip(),
        description=description or None,
        status=status,
        issue_type=issue_type,
    )
