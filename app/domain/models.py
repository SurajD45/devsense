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
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Validation Helpers & Constants
# ---------------------------------------------------------------------------

_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
_JIRA_KEY_PATTERN = re.compile(r"^[A-Z0-9_]+-[0-9]+$")
_CRITERION_ID_PATTERN = re.compile(r"^AC-[0-9]+$")
_EVIDENCE_ID_PATTERN = re.compile(r"^EV-[0-9]+$")
_FINDING_ID_PATTERN = re.compile(r"^FIND-[0-9]+$")
_INVESTIGATION_ID_PATTERN = re.compile(r"^INV-[0-9]+$")


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


# ---------------------------------------------------------------------------
# 5. AcceptanceCriterion
# ---------------------------------------------------------------------------

class AcceptanceCriterion(BaseModel):
    """
    Represents a single, testable acceptance criterion for a requirement.

    This is a provider-independent domain model. Acceptance criteria are
    first-class objects rather than an opaque string, enabling future
    agent processing and traceability mapping.
    """

    criterion_id: str
    title: str
    description: str | None = None
    is_met: bool = False

    @field_validator("criterion_id")
    @classmethod
    def _validate_criterion_id(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "criterion_id")
        if not _CRITERION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"criterion_id '{value}' is invalid; "
                "must match pattern 'AC-<number>' (e.g. 'AC-1', 'AC-42')"
            )
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


# ---------------------------------------------------------------------------
# 6. Requirement
# ---------------------------------------------------------------------------

class Requirement(BaseModel):
    """
    Represents a provider-independent software requirement within DevSense.

    A Requirement is the top-level traceability anchor. It contains multiple
    AcceptanceCriterion objects that define testable conditions of satisfaction.
    This model is intentionally decoupled from any specific issue tracker
    (Jira, GitHub Issues, etc.).
    """

    requirement_id: str
    title: str
    description: str | None = None
    source: str | None = None
    priority: str | None = None
    status: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)

    @field_validator("requirement_id")
    @classmethod
    def _validate_requirement_id(cls, value: str) -> str:
        return _validate_non_empty_str(value, "requirement_id")

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

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("source must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("priority must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        return _validate_non_empty_str(value, "status")


# ---------------------------------------------------------------------------
# 7. EvidenceType
# ---------------------------------------------------------------------------

class EvidenceType(str, Enum):
    """
    Categorizes the kind of artifact an Evidence object references.
    """

    CODE = "CODE"
    TEST = "TEST"
    DOCUMENTATION = "DOCUMENTATION"
    CI = "CI"
    REQUIREMENT = "REQUIREMENT"


# ---------------------------------------------------------------------------
# 8. Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """
    Represents a lightweight reference to a concrete repository or requirement
    artifact that an investigation can cite.

    Evidence is strictly a reference — it stores file paths, line ranges,
    and symbol names, but never full file contents, patches, or large
    code excerpts.  It is provider-independent and contains no judgment
    fields (validity, relevance, confidence); those belong to future
    Finding/Investigation models.
    """

    evidence_id: str
    evidence_type: EvidenceType
    repository: str | None = None
    file_path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    symbol: str | None = None
    commit_sha: str | None = None
    description: str

    @field_validator("evidence_id")
    @classmethod
    def _validate_evidence_id(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "evidence_id")
        if not _EVIDENCE_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"evidence_id '{value}' is invalid; "
                "must match pattern 'EV-<number>' (e.g. 'EV-1', 'EV-42')"
            )
        return cleaned

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("repository must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("file_path must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("symbol must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("commit_sha must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _validate_non_empty_str(value, "description")

    @model_validator(mode="after")
    def _validate_line_range(self) -> Evidence:
        """Ensure end_line >= start_line when both are provided."""
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError(
                    f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                )
        return self


# ---------------------------------------------------------------------------
# 9. FindingStatus
# ---------------------------------------------------------------------------

class FindingStatus(str, Enum):
    """
    Represents the verification outcome of an investigation finding.
    """

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


# ---------------------------------------------------------------------------
# 10. Finding
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """
    Represents an investigation conclusion based on concrete Evidence,
    optionally associated with an AcceptanceCriterion.

    A Finding is a judgment — it captures the reasoning and verification
    status (for a criterion or cross-cutting concern), referencing Evidence
    by ID rather than embedding it.  This keeps Finding lightweight and avoids
    duplicating artifact data.

    This model is provider-independent and contains no severity,
    confidence, recommendation, agent, or LLM fields.
    """

    finding_id: str
    criterion_id: str | None = None
    status: FindingStatus
    title: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("finding_id")
    @classmethod
    def _validate_finding_id(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "finding_id")
        if not _FINDING_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"finding_id '{value}' is invalid; "
                "must match pattern 'FIND-<number>' (e.g. 'FIND-1', 'FIND-42')"
            )
        return cleaned

    @field_validator("criterion_id")
    @classmethod
    def _validate_criterion_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("criterion_id must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _validate_non_empty_str(value, "title")

    @field_validator("explanation")
    @classmethod
    def _validate_explanation(cls, value: str) -> str:
        return _validate_non_empty_str(value, "explanation")

    @field_validator("evidence_ids")
    @classmethod
    def _validate_evidence_ids(cls, value: list[str]) -> list[str]:
        validated: list[str] = []
        for i, eid in enumerate(value):
            if not isinstance(eid, str):
                raise ValueError(
                    f"evidence_ids[{i}] must be a string"
                )
            cleaned = eid.strip()
            if not _EVIDENCE_ID_PATTERN.fullmatch(cleaned):
                raise ValueError(
                    f"evidence_ids[{i}] '{eid}' is invalid; "
                    "must match pattern 'EV-<number>' (e.g. 'EV-1', 'EV-42')"
                )
            validated.append(cleaned)
        return validated


# ---------------------------------------------------------------------------
# 11. InvestigationStatus
# ---------------------------------------------------------------------------

class InvestigationStatus(str, Enum):
    """
    Represents the lifecycle state of a DevSense investigation.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# 12. Investigation
# ---------------------------------------------------------------------------

class Investigation(BaseModel):
    """
    Represents one DevSense analysis run for a single Pull Request.

    An Investigation is an orchestration-level domain object that ties
    together a PullRequest, a Requirement, and the resulting Findings.
    It references all related objects by identifier rather than embedding
    them, keeping the model lightweight and avoiding data duplication.

    This model is provider-independent and contains no agent, LLM,
    token-count, prompt, or confidence fields.
    """

    investigation_id: str
    pull_request_number: int = Field(ge=1)
    requirement_id: str
    status: InvestigationStatus
    finding_ids: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("investigation_id")
    @classmethod
    def _validate_investigation_id(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "investigation_id")
        if not _INVESTIGATION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"investigation_id '{value}' is invalid; "
                "must match pattern 'INV-<number>' (e.g. 'INV-1', 'INV-42')"
            )
        return cleaned

    @field_validator("requirement_id")
    @classmethod
    def _validate_requirement_id(cls, value: str) -> str:
        return _validate_non_empty_str(value, "requirement_id")

    @field_validator("finding_ids")
    @classmethod
    def _validate_finding_ids(cls, value: list[str]) -> list[str]:
        validated: list[str] = []
        for i, fid in enumerate(value):
            if not isinstance(fid, str):
                raise ValueError(
                    f"finding_ids[{i}] must be a string"
                )
            cleaned = fid.strip()
            if not _FINDING_ID_PATTERN.fullmatch(cleaned):
                raise ValueError(
                    f"finding_ids[{i}] '{fid}' is invalid; "
                    "must match pattern 'FIND-<number>' (e.g. 'FIND-1', 'FIND-42')"
                )
            validated.append(cleaned)
        return validated
