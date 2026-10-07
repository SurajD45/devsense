"""Internal Jira representations used by the DevSense Jira integration."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class JiraIssue:
    """Small, normalized representation of a Jira issue."""

    key: str
    summary: str
    description: Optional[str]
    status: Optional[str]
    issue_type: Optional[str]
    priority: Optional[str] = None
    acceptance_criteria: list[str] = field(default_factory=list)

