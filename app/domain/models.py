"""
DevSense — Domain Models (V1)
=============================
Internal normalized data contracts for DevSense.

These models decouple external integrations (GitHub, Jira) from downstream
components (investigation, reporting, AI analysis).

Security & Purity:
  - Domain models contain zero credentials, tokens, or private keys.
  - Domain models are pure data contracts independent of external APIs,
    HTTP clients, configuration, or database systems.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Validation Helpers & Constants
# ---------------------------------------------------------------------------

_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
_JIRA_KEY_PATTERN = re.compile(r"^[A-Z0-9_]+-[0-9]+$")


def _validate_non_empty_str(value: str, field_name: str) -> str:
    """Validate that value is a string and not empty or whitespace-only."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty or whitespace-only")
    return cleaned


# ---------------------------------------------------------------------------
# 1. Commit
# ---------------------------------------------------------------------------

class Commit(BaseModel):
    """
    Represents an individual Git commit associated with a pull request.
    """

    sha: str
    message: str
    author: str

    @field_validator("sha")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("sha must be a string")
        cleaned = value.strip()
        if not _HEX_SHA_PATTERN.fullmatch(cleaned):
            raise ValueError(
                "sha must contain hexadecimal characters only and be between 7 and 40 characters in length"
            )
        return cleaned.lower()

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        return _validate_non_empty_str(value, "message")

    @field_validator("author")
    @classmethod
    def _validate_author(cls, value: str) -> str:
        return _validate_non_empty_str(value, "author")


# ---------------------------------------------------------------------------
# 2. ChangedFile
# ---------------------------------------------------------------------------

class ChangedFile(BaseModel):
    """
    Represents a single file modified within a pull request.
    """

    path: str
    status: str
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    patch: str | None = None

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "path")

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "status")
        return cleaned.lower()


# ---------------------------------------------------------------------------
# 3. PullRequest
# ---------------------------------------------------------------------------

class PullRequest(BaseModel):
    """
    Represents a normalized pull request within DevSense.
    """

    repository: str
    number: int = Field(gt=0)
    title: str
    description: str | None = None
    author: str
    source_branch: str
    target_branch: str
    state: str
    commits: list[Commit] = Field(default_factory=list)
    changed_files: list[ChangedFile] = Field(default_factory=list)

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "repository")
        if "/" not in cleaned:
            raise ValueError("repository must be in 'owner/repo' format and contain '/'")
        return cleaned

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _validate_non_empty_str(value, "title")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("description must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("author")
    @classmethod
    def _validate_author(cls, value: str) -> str:
        return _validate_non_empty_str(value, "author")

    @field_validator("source_branch")
    @classmethod
    def _validate_source_branch(cls, value: str) -> str:
        return _validate_non_empty_str(value, "source_branch")

    @field_validator("target_branch")
    @classmethod
    def _validate_target_branch(cls, value: str) -> str:
        return _validate_non_empty_str(value, "target_branch")

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "state")
        return cleaned.lower()


# ---------------------------------------------------------------------------
# 4. JiraIssue
# ---------------------------------------------------------------------------

class JiraIssue(BaseModel):
    """
    Represents a normalized project management ticket within DevSense.
    """

    issue_key: str
    summary: str
    description: str | None = None
    status: str
    priority: str | None = None
    issue_type: str
    acceptance_criteria: str | None = None

    @field_validator("issue_key")
    @classmethod
    def _validate_issue_key(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "issue_key").upper()
        if not _JIRA_KEY_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"issue_key '{value}' is invalid; must match pattern '^[A-Z0-9_]+-[0-9]+$'"
            )
        return cleaned

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _validate_non_empty_str(value, "summary")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("description must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        return _validate_non_empty_str(value, "status")

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("priority must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("issue_type")
    @classmethod
    def _validate_issue_type(cls, value: str) -> str:
        return _validate_non_empty_str(value, "issue_type")

    @field_validator("acceptance_criteria")
    @classmethod
    def _validate_acceptance_criteria(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("acceptance_criteria must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

