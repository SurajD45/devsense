"""
DevSense — Investigation Schemas
================================
Application-layer data transfer objects (DTOs) and pipeline contracts
used during DevSense PR investigations.

These models are intermediate pipeline contracts — they represent
analysis stages (such as requirement interpretation) and are distinct
from canonical, persistent domain models in app.domain.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.models import (
    Evidence,
    Finding,
    Investigation,
    InvestigationStatus,
    PullRequest,
    Requirement,
)

# ---------------------------------------------------------------------------
# Validation Helpers & Patterns
# ---------------------------------------------------------------------------

_CRITERION_ID_PATTERN = re.compile(r"^AC-[0-9]+$")
_EVIDENCE_ID_PATTERN = re.compile(r"^EV-[0-9]+$")
_FINDING_ID_PATTERN = re.compile(r"^FIND-[0-9]+$")
_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
_INVESTIGATION_ID_PATTERN = re.compile(r"^INV-[0-9]+$")
_PATH_SEPARATORS_PATTERN = re.compile(r"[/\\]")

# Known file extensions to catch direct repository file references (e.g. "auth.py")
# while allowing legitimate conceptual names with dots (e.g. "auth.service", "v1.2").
_KNOWN_FILE_EXTENSIONS = (
    ".py",
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".go",
    ".java",
    ".rs",
    ".rb",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".php",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".css",
    ".sql",
    ".sh",
)


def _validate_non_empty_str(value: str, field_name: str) -> str:
    """Validate that value is a string and not empty or whitespace-only."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty or whitespace-only")
    return cleaned


# ---------------------------------------------------------------------------
# AnalyzedCriterion
# ---------------------------------------------------------------------------

class AnalyzedCriterion(BaseModel):
    """
    Structured operational analysis of a single AcceptanceCriterion.

    Contains operational interpretations, concrete conditions to verify,
    and conceptual subsystems — strictly decoupling acceptance logic
    from specific files or implementation locations.
    """

    criterion_id: str
    interpretation: str
    verification_points: list[str] = Field(min_length=1)
    potential_areas: list[str] = Field(default_factory=list)

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

    @field_validator("interpretation")
    @classmethod
    def _validate_interpretation(cls, value: str) -> str:
        return _validate_non_empty_str(value, "interpretation")

    @field_validator("verification_points")
    @classmethod
    def _validate_verification_points(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("verification_points must contain at least one item")
        cleaned_points: list[str] = []
        for i, point in enumerate(values):
            cleaned = _validate_non_empty_str(point, f"verification_points[{i}]")
            cleaned_points.append(cleaned)
        return cleaned_points

    @field_validator("potential_areas")
    @classmethod
    def _validate_potential_areas(cls, values: list[str]) -> list[str]:
        cleaned_areas: list[str] = []
        for i, area in enumerate(values):
            cleaned = _validate_non_empty_str(area, f"potential_areas[{i}]")

            # Must represent conceptual areas/components, not file paths
            if _PATH_SEPARATORS_PATTERN.search(cleaned):
                raise ValueError(
                    f"potential_areas[{i}] '{area}' contains path separators; "
                    "potential_areas must represent conceptual components, not file paths"
                )
            if cleaned.lower().endswith(_KNOWN_FILE_EXTENSIONS):
                raise ValueError(
                    f"potential_areas[{i}] '{area}' appears to be a specific file; "
                    "potential_areas must represent conceptual components, not file paths"
                )

            cleaned_areas.append(cleaned)
        return cleaned_areas


# ---------------------------------------------------------------------------
# RequirementAnalysis
# ---------------------------------------------------------------------------

class RequirementAnalysis(BaseModel):
    """
    Structured outcome of the requirement analysis phase for a Requirement.

    Represents the operational breakdown of all acceptance criteria
    before evaluating pull request diffs or repository files.
    """

    requirement_id: str
    summary: str
    criteria: list[AnalyzedCriterion] = Field(default_factory=list)

    @field_validator("requirement_id")
    @classmethod
    def _validate_requirement_id(cls, value: str) -> str:
        return _validate_non_empty_str(value, "requirement_id")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _validate_non_empty_str(value, "summary")

    @field_validator("criteria")
    @classmethod
    def _validate_criteria(cls, values: list[AnalyzedCriterion]) -> list[AnalyzedCriterion]:
        seen_ids: set[str] = set()
        for criterion in values:
            if criterion.criterion_id in seen_ids:
                raise ValueError(
                    f"Duplicate criterion_id '{criterion.criterion_id}' found in criteria"
                )
            seen_ids.add(criterion.criterion_id)
        return values


# ---------------------------------------------------------------------------
# FileClassification
# ---------------------------------------------------------------------------

class FileClassification(str, Enum):
    """
    Classification of a changed file's relationship to a requirement.

    PRIMARY   — directly related to requirement verification.
    SECONDARY — potentially related, supporting, or not yet determined.
    """

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


# ---------------------------------------------------------------------------
# ChangedFileClassification
# ---------------------------------------------------------------------------

class ChangedFileClassification(BaseModel):
    """
    Classification result for a single changed file in a PR.

    Every changed file in the PR must appear exactly once in the
    ChangeImpactAnalysis output.  Files that cannot be confidently
    classified as PRIMARY must be classified as SECONDARY rather
    than being discarded.
    """

    file_path: str
    classification: FileClassification
    reasons: list[str] = Field(min_length=1)

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")

    @field_validator("reasons")
    @classmethod
    def _validate_reasons(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("reasons must contain at least one entry")
        cleaned: list[str] = []
        for i, reason in enumerate(values):
            cleaned.append(_validate_non_empty_str(reason, f"reasons[{i}]"))
        return cleaned


# ---------------------------------------------------------------------------
# ChangeImpactAnalysis
# ---------------------------------------------------------------------------

class ChangeImpactAnalysis(BaseModel):
    """
    Structured result of the change / impact analysis phase.

    Contains one ChangedFileClassification per changed file in the PR.
    Every input file must be represented — none may be silently dropped.
    """

    classifications: list[ChangedFileClassification] = Field(default_factory=list)

    @field_validator("classifications")
    @classmethod
    def _validate_classifications(
        cls, values: list[ChangedFileClassification],
    ) -> list[ChangedFileClassification]:
        seen_paths: set[str] = set()
        for entry in values:
            if entry.file_path in seen_paths:
                raise ValueError(
                    f"Duplicate file_path '{entry.file_path}' in classifications"
                )
            seen_paths.add(entry.file_path)
        return values


# ---------------------------------------------------------------------------
# FileReviewPriority
# ---------------------------------------------------------------------------

class FileReviewPriority(str, Enum):
    """
    Review priority assigned to a changed file after secondary-file screening.

    PRIMARY   — directly related to requirement verification (from initial classification).
    SECONDARY — supporting or unrelated file not meeting promotion criteria.
    PROMOTED  — SECONDARY file promoted for deeper investigation based on deterministic signals.

    IMPORTANT SEMANTIC RULE:
    PROMOTED means only: 'This file deserves deeper investigation.'
    It does NOT imply that the file is buggy, unsafe, or violates the requirement.
    """

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    PROMOTED = "PROMOTED"


# ---------------------------------------------------------------------------
# FileReviewItem
# ---------------------------------------------------------------------------

class FileReviewItem(BaseModel):
    """
    Review decision for a single changed file in a PR after promotion screening.

    Every changed file must appear exactly once in the FilePromotionAnalysis.
    A promoted file remains traceable to its original classification as SECONDARY.
    """

    file_path: str
    priority: FileReviewPriority
    reasons: list[str] = Field(min_length=1)
    original_classification: FileClassification = FileClassification.SECONDARY

    @model_validator(mode="before")
    @classmethod
    def _set_default_original_classification(cls, data: Any) -> Any:
        if isinstance(data, dict):
            priority = data.get("priority")
            if "original_classification" not in data or data["original_classification"] is None:
                if priority in (FileReviewPriority.PRIMARY, "PRIMARY"):
                    data["original_classification"] = FileClassification.PRIMARY
                else:
                    data["original_classification"] = FileClassification.SECONDARY
        return data

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")

    @field_validator("reasons")
    @classmethod
    def _validate_reasons(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("reasons must contain at least one entry")
        cleaned: list[str] = []
        for i, reason in enumerate(values):
            cleaned.append(_validate_non_empty_str(reason, f"reasons[{i}]"))
        return cleaned

    @model_validator(mode="after")
    def _validate_classification_consistency(self) -> "FileReviewItem":
        if self.priority == FileReviewPriority.PRIMARY:
            if self.original_classification != FileClassification.PRIMARY:
                raise ValueError(
                    "PRIMARY priority items must have original_classification=FileClassification.PRIMARY"
                )
        elif self.priority == FileReviewPriority.PROMOTED:
            if self.original_classification != FileClassification.SECONDARY:
                raise ValueError(
                    "Only SECONDARY files can be promoted; original_classification must be FileClassification.SECONDARY"
                )
        return self


# Backward-compatible and contextual aliases
PrioritizedFile = FileReviewItem
ChangedFileReview = FileReviewItem


# ---------------------------------------------------------------------------
# FilePromotionAnalysis
# ---------------------------------------------------------------------------

class FilePromotionAnalysis(BaseModel):
    """
    Structured outcome of the suspicious-file promotion screening phase.

    Contains one FileReviewItem per changed file in the PR.
    Every changed file must appear exactly once — none may be dropped.
    """

    files: list[FileReviewItem] = Field(default_factory=list)

    @field_validator("files")
    @classmethod
    def _validate_files(cls, values: list[FileReviewItem]) -> list[FileReviewItem]:
        seen_paths: set[str] = set()
        for item in values:
            if item.file_path in seen_paths:
                raise ValueError(
                    f"Duplicate file_path '{item.file_path}' in files"
                )
            seen_paths.add(item.file_path)
        return values

    @property
    def items(self) -> list[FileReviewItem]:
        return self.files

    @property
    def primary_files(self) -> list[FileReviewItem]:
        return [f for f in self.files if f.priority == FileReviewPriority.PRIMARY]

    @property
    def secondary_files(self) -> list[FileReviewItem]:
        return [f for f in self.files if f.priority == FileReviewPriority.SECONDARY]

    @property
    def promoted_files(self) -> list[FileReviewItem]:
        return [f for f in self.files if f.priority == FileReviewPriority.PROMOTED]


# Contextual alias
FileReviewAnalysis = FilePromotionAnalysis


# ---------------------------------------------------------------------------
# CodeObservationType
# ---------------------------------------------------------------------------

class CodeObservationType(str, Enum):
    """
    Categorization of a code-level observation.

    Observations record concrete code characteristics and potential areas of interest
    without asserting verification conclusions or findings.
    """

    LOGIC = "LOGIC"
    CONTROL_FLOW = "CONTROL_FLOW"
    DATA_FLOW = "DATA_FLOW"
    API = "API"
    ERROR_HANDLING = "ERROR_HANDLING"
    SECURITY = "SECURITY"
    CONFIGURATION = "CONFIGURATION"
    TESTABILITY = "TESTABILITY"
    UNKNOWN = "UNKNOWN"


ObservationType = CodeObservationType

_OBSERVATION_ID_PATTERN = re.compile(r"^CODE-[0-9]+$")


# ---------------------------------------------------------------------------
# CodeObservation
# ---------------------------------------------------------------------------

class CodeObservation(BaseModel):
    """
    A concrete code-level observation recorded during code investigation.

    IMPORTANT SEMANTIC DISTINCTION:
    An Observation is NOT a Finding.
    Observations record factual code characteristics (e.g. TODO marker, bare except,
    potential credential pattern) without asserting whether an acceptance criterion
    is satisfied or violated.
    """

    observation_id: str
    file_path: str
    description: str
    symbol: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    observation_type: CodeObservationType
    criterion_id: str | None = None
    criterion_ids: list[str] = Field(default_factory=list)

    @field_validator("observation_id")
    @classmethod
    def _validate_observation_id(cls, value: str) -> str:
        cleaned = _validate_non_empty_str(value, "observation_id")
        if not _OBSERVATION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"observation_id '{value}' is invalid; must match pattern 'CODE-<number>' (e.g. 'CODE-1')"
            )
        return cleaned

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _validate_non_empty_str(value, "description")

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("criterion_id")
    @classmethod
    def _validate_criterion_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _validate_non_empty_str(value, "criterion_id")
        if not _CRITERION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"criterion_id '{value}' is invalid; must match pattern 'AC-<number>' (e.g. 'AC-1')"
            )
        return cleaned

    @field_validator("criterion_ids")
    @classmethod
    def _validate_criterion_ids(cls, values: list[str]) -> list[str]:
        cleaned_list: list[str] = []
        seen: set[str] = set()
        for i, val in enumerate(values):
            cleaned = _validate_non_empty_str(val, f"criterion_ids[{i}]")
            if not _CRITERION_ID_PATTERN.fullmatch(cleaned):
                raise ValueError(
                    f"criterion_ids[{i}] '{val}' is invalid; must match pattern 'AC-<number>' (e.g. 'AC-1')"
                )
            if cleaned not in seen:
                seen.add(cleaned)
                cleaned_list.append(cleaned)
        return cleaned_list

    @model_validator(mode="after")
    def _validate_line_range_and_criteria(self) -> "CodeObservation":
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError(
                    f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                )
        if self.criterion_id is not None and not self.criterion_ids:
            self.criterion_ids = [self.criterion_id]
        elif self.criterion_ids and self.criterion_id is None:
            self.criterion_id = self.criterion_ids[0]
        elif self.criterion_id is not None and self.criterion_ids:
            if self.criterion_id != self.criterion_ids[0]:
                if self.criterion_id in self.criterion_ids:
                    self.criterion_ids.remove(self.criterion_id)
                self.criterion_ids.insert(0, self.criterion_id)
        return self


# ---------------------------------------------------------------------------
# CodeInvestigationResult
# ---------------------------------------------------------------------------

class CodeInvestigationResult(BaseModel):
    """
    Structured outcome of investigating a single changed file's content.

    Contains all code-level observations recorded for that file.
    """

    file_path: str
    observations: list[CodeObservation] = Field(default_factory=list)

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")

    @field_validator("observations")
    @classmethod
    def _validate_observations(
        cls, values: list[CodeObservation]
    ) -> list[CodeObservation]:
        seen_ids: set[str] = set()
        for obs in values:
            if obs.observation_id in seen_ids:
                raise ValueError(
                    f"Duplicate observation_id '{obs.observation_id}' found in observations"
                )
            seen_ids.add(obs.observation_id)
        return values

    @model_validator(mode="after")
    def _validate_observation_file_paths(self) -> "CodeInvestigationResult":
        for obs in self.observations:
            if obs.file_path != self.file_path:
                raise ValueError(
                    f"Observation file_path '{obs.file_path}' does not match result file_path '{self.file_path}'"
                )
        return self


# ---------------------------------------------------------------------------
# RequirementAwareCodeInvestigationInput
# ---------------------------------------------------------------------------

class RequirementAwareCodeInvestigationInput(BaseModel):
    """
    Input payload for requirement-aware code investigation.

    Supplies the requirement analysis context against which a changed
    file's content is analyzed for relevant observations.
    """

    requirement_analysis: RequirementAnalysis
    file_path: str
    file_content: str
    review_priority: FileReviewPriority | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_content_field(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "content" in data and "file_content" not in data:
                data["file_content"] = data["content"]
        return data

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")


# ---------------------------------------------------------------------------
# CodeEvidenceBuildResult
# ---------------------------------------------------------------------------

class CodeEvidenceBuildResult(BaseModel):
    """
    Structured outcome of building domain Evidence objects from code observations.

    Preserves traceability between observations and generated Evidence references
    without modifying the domain Evidence model.
    """

    evidence: list[Evidence] = Field(default_factory=list)
    observation_to_evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def _validate_evidence(cls, values: list[Evidence]) -> list[Evidence]:
        seen: set[str] = set()
        for ev in values:
            if not isinstance(ev, Evidence):
                raise TypeError(f"Expected Evidence instance, got {type(ev).__name__}")
            if ev.evidence_id in seen:
                raise ValueError(
                    f"Duplicate evidence_id '{ev.evidence_id}' found in evidence list"
                )
            seen.add(ev.evidence_id)
        return values

    @field_validator("observation_to_evidence")
    @classmethod
    def _validate_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        for obs_id, ev_id in value.items():
            if not isinstance(obs_id, str) or not _OBSERVATION_ID_PATTERN.fullmatch(obs_id):
                raise ValueError(
                    f"observation_id key '{obs_id}' is invalid; must match pattern 'CODE-<number>'"
                )
            if not isinstance(ev_id, str) or not _EVIDENCE_ID_PATTERN.fullmatch(ev_id):
                raise ValueError(
                    f"evidence_id value '{ev_id}' is invalid; must match pattern 'EV-<number>'"
                )
        return value

    @model_validator(mode="after")
    def _validate_traceability(self) -> "CodeEvidenceBuildResult":
        evidence_ids = {ev.evidence_id for ev in self.evidence}
        mapped_evidence_ids = set(self.observation_to_evidence.values())
        if len(self.evidence) != len(self.observation_to_evidence):
            raise ValueError(
                f"Mismatch between number of evidence objects ({len(self.evidence)}) "
                f"and observation mappings ({len(self.observation_to_evidence)})"
            )
        if evidence_ids != mapped_evidence_ids:
            raise ValueError(
                "Evidence IDs in evidence list do not match IDs in observation_to_evidence mapping"
            )
        return self

    @property
    def evidence_to_observation(self) -> dict[str, str]:
        """Reverse mapping from evidence_id to observation_id."""
        return {ev_id: obs_id for obs_id, ev_id in self.observation_to_evidence.items()}

    def __iter__(self):
        return iter(self.evidence)

    def __len__(self) -> int:
        return len(self.evidence)

    def __getitem__(self, index: int | slice):
        return self.evidence[index]


# ---------------------------------------------------------------------------
# TestExecutionStatus
# ---------------------------------------------------------------------------

class TestExecutionStatus(str, Enum):
    """
    Categorizes the execution outcome of an automated test case.
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_RUN = "NOT_RUN"


TestExecutionStatus.__test__ = False


# ---------------------------------------------------------------------------
# TestExecutionRecord
# ---------------------------------------------------------------------------

class TestExecutionRecord(BaseModel):
    """
    Structured outcome of an individual automated test case execution.

    Represents factual test telemetry (e.g. from CI/CD runners or test frameworks),
    strictly decoupled from requirement verification judgments.
    """

    __test__ = False

    test_id: str
    file_path: str
    test_name: str
    status: TestExecutionStatus
    criterion_ids: list[str] = Field(default_factory=list)
    duration_ms: float | None = Field(default=None, ge=0.0)
    failure_message: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    commit_sha: str | None = None

    @field_validator("test_id")
    @classmethod
    def _validate_test_id(cls, value: str) -> str:
        return _validate_non_empty_str(value, "test_id")

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        return _validate_non_empty_str(value, "file_path")

    @field_validator("test_name")
    @classmethod
    def _validate_test_name(cls, value: str) -> str:
        return _validate_non_empty_str(value, "test_name")

    @field_validator("criterion_ids")
    @classmethod
    def _validate_criterion_ids(cls, values: list[str]) -> list[str]:
        cleaned_list: list[str] = []
        seen: set[str] = set()
        for i, val in enumerate(values):
            cleaned = _validate_non_empty_str(val, f"criterion_ids[{i}]")
            if not _CRITERION_ID_PATTERN.fullmatch(cleaned):
                raise ValueError(
                    f"criterion_ids[{i}] '{val}' is invalid; "
                    "must match pattern 'AC-<number>' (e.g. 'AC-1', 'AC-42')"
                )
            if cleaned not in seen:
                seen.add(cleaned)
                cleaned_list.append(cleaned)
        return cleaned_list

    @field_validator("duration_ms")
    @classmethod
    def _validate_duration_ms(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or value < 0.0:
            raise ValueError("duration_ms must be a non-negative number")
        return float(value)

    @field_validator("failure_message")
    @classmethod
    def _validate_failure_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("failure_message must be a string or None")
        cleaned = value.strip()
        return cleaned if cleaned else None

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _validate_non_empty_str(value, "commit_sha")
        if not _HEX_SHA_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"commit_sha '{value}' is invalid; must be a hexadecimal Git commit SHA (7 to 40 characters)"
            )
        return cleaned.lower()

    @model_validator(mode="after")
    def _validate_line_range(self) -> "TestExecutionRecord":
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError(
                    f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
                )
        return self


# ---------------------------------------------------------------------------
# TestEvidenceBuildResult
# ---------------------------------------------------------------------------

class TestEvidenceBuildResult(BaseModel):
    """
    Structured outcome of building domain Evidence objects from test execution records.

    Preserves 1-to-1 traceability between test records and generated Evidence
    references without modifying the canonical domain Evidence model.
    """

    __test__ = False

    evidence: list[Evidence] = Field(default_factory=list)
    test_to_evidence: dict[str, str] = Field(default_factory=dict)
    test_records: list[TestExecutionRecord] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _validate_evidence(cls, values: list[Evidence]) -> list[Evidence]:
        seen: set[str] = set()
        for ev in values:
            if not isinstance(ev, Evidence):
                raise TypeError(f"Expected Evidence instance, got {type(ev).__name__}")
            if ev.evidence_id in seen:
                raise ValueError(
                    f"Duplicate evidence_id '{ev.evidence_id}' found in evidence list"
                )
            seen.add(ev.evidence_id)
        return values

    @field_validator("test_records")
    @classmethod
    def _validate_test_records(
        cls, values: list[TestExecutionRecord]
    ) -> list[TestExecutionRecord]:
        seen: set[str] = set()
        for tr in values:
            if not isinstance(tr, TestExecutionRecord):
                raise TypeError(
                    f"Expected TestExecutionRecord instance, got {type(tr).__name__}"
                )
            if tr.test_id in seen:
                raise ValueError(
                    f"Duplicate test_id '{tr.test_id}' found in test_records list"
                )
            seen.add(tr.test_id)
        return values

    @field_validator("test_to_evidence")
    @classmethod
    def _validate_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        for test_id, ev_id in value.items():
            _validate_non_empty_str(test_id, "test_to_evidence key")
            if not isinstance(ev_id, str) or not _EVIDENCE_ID_PATTERN.fullmatch(ev_id):
                raise ValueError(
                    f"evidence_id value '{ev_id}' is invalid; must match pattern 'EV-<number>'"
                )
        return value

    @model_validator(mode="after")
    def _validate_traceability(self) -> "TestEvidenceBuildResult":
        record_test_ids = {tr.test_id for tr in self.test_records}
        evidence_ids = {ev.evidence_id for ev in self.evidence}
        mapped_test_ids = set(self.test_to_evidence.keys())
        mapped_ev_ids = set(self.test_to_evidence.values())

        # 1. Traceability mappings must reference existing test records
        unknown_tests = mapped_test_ids - record_test_ids
        if unknown_tests:
            raise ValueError(
                f"test_to_evidence references unknown test_id(s) not in test_records: {unknown_tests}"
            )

        # 2. All test records must be mapped in test_to_evidence
        unmapped_tests = record_test_ids - mapped_test_ids
        if unmapped_tests:
            raise ValueError(
                f"test_records contains test_id(s) missing from test_to_evidence mapping: {unmapped_tests}"
            )

        # 3. Evidence IDs in evidence list must match mapped evidence IDs
        if mapped_ev_ids != evidence_ids:
            raise ValueError(
                "Evidence IDs in evidence list do not match IDs in test_to_evidence mapping"
            )

        # 4. Require the number of test records, mapping entries, and evidence objects to agree
        if len(self.test_records) != len(self.test_to_evidence):
            raise ValueError(
                f"Mismatch between number of test records ({len(self.test_records)}) "
                f"and test mappings ({len(self.test_to_evidence)})"
            )

        if len(self.evidence) != len(self.test_to_evidence):
            raise ValueError(
                f"Mismatch between number of evidence objects ({len(self.evidence)}) "
                f"and test mappings ({len(self.test_to_evidence)})"
            )

        return self

    @property
    def evidence_to_test(self) -> dict[str, str]:
        """Reverse mapping from evidence_id to test_id."""
        return {ev_id: test_id for test_id, ev_id in self.test_to_evidence.items()}

    def __iter__(self):
        return iter(self.evidence)

    def __len__(self) -> int:
        return len(self.evidence)

    def __getitem__(self, index: int | slice):
        return self.evidence[index]


# ---------------------------------------------------------------------------
# InvestigationPipelineInput
# ---------------------------------------------------------------------------

class InvestigationPipelineInput(BaseModel):
    """
    Structured input contract for the deterministic investigation pipeline.

    Encapsulates all necessary inputs required by the pipeline stages:
    - The normalized pull request to be investigated.
    - The linked specification requirement containing acceptance criteria.
    - An optional in-memory mapping of repository file paths to file contents.
    - An optional collection of structured test execution records.
    - An optional explicit investigation identifier.
    """

    pull_request: PullRequest
    requirement: Requirement
    file_contents: dict[str, str] = Field(default_factory=dict)
    test_results: list[TestExecutionRecord] = Field(default_factory=list)
    investigation_id: str | None = None

    @field_validator("investigation_id")
    @classmethod
    def _validate_investigation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _validate_non_empty_str(value, "investigation_id")
        if not _INVESTIGATION_ID_PATTERN.fullmatch(cleaned):
            raise ValueError(
                f"investigation_id '{value}' is invalid; "
                "must match pattern 'INV-<number>' (e.g. 'INV-1', 'INV-42')"
            )
        return cleaned

    @field_validator("file_contents")
    @classmethod
    def _validate_file_contents(cls, value: dict[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for file_path, content in value.items():
            cleaned_path = _validate_non_empty_str(file_path, "file_contents key")
            if not isinstance(content, str):
                raise ValueError(f"Content for file '{file_path}' must be a string")
            if cleaned_path in validated:
                raise ValueError(
                    f"Duplicate or colliding file path '{cleaned_path}' in file_contents"
                )
            validated[cleaned_path] = content
        return validated

    @field_validator("requirement")
    @classmethod
    def _validate_requirement_criteria(cls, value: Requirement) -> Requirement:
        if not value.acceptance_criteria:
            raise ValueError(
                "requirement must contain at least one AcceptanceCriterion for investigation"
            )
        return value

    @field_validator("test_results")
    @classmethod
    def _validate_test_results(
        cls, values: list[TestExecutionRecord]
    ) -> list[TestExecutionRecord]:
        seen_test_ids: set[str] = set()
        for idx, tr in enumerate(values):
            if not isinstance(tr, TestExecutionRecord):
                raise TypeError(
                    f"Expected TestExecutionRecord instance, got {type(tr).__name__} at index {idx}"
                )
            if tr.test_id in seen_test_ids:
                raise ValueError(
                    f"Duplicate test_id '{tr.test_id}' in test_results"
                )
            seen_test_ids.add(tr.test_id)
        return values


# ---------------------------------------------------------------------------
# InvestigationResultBundle
# ---------------------------------------------------------------------------

class InvestigationResultBundle(BaseModel):
    """
    Structured output bundle produced by the deterministic investigation pipeline.

    Ties together the final orchestration Investigation domain record, the generated
    Findings, and the referenced concrete Evidence artifacts, while retaining optional
    intermediate stage analysis outputs for full auditability and explainability.
    """

    investigation: Investigation
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    # Optional intermediate stage outputs for full pipeline observability
    requirement_analysis: RequirementAnalysis | None = None
    impact_analysis: ChangeImpactAnalysis | None = None
    promotion_analysis: FilePromotionAnalysis | None = None
    code_investigation_results: list[CodeInvestigationResult] = Field(default_factory=list)
    evidence_build_result: CodeEvidenceBuildResult | None = None
    test_evidence_build_result: TestEvidenceBuildResult | None = None

    # Execution / diagnostic errors
    errors: list[str] = Field(default_factory=list)

    @field_validator("errors")
    @classmethod
    def _validate_errors(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for i, err in enumerate(values):
            cleaned.append(_validate_non_empty_str(err, f"errors[{i}]"))
        return cleaned

    @field_validator("findings")
    @classmethod
    def _validate_findings(cls, values: list[Finding]) -> list[Finding]:
        seen_ids: set[str] = set()
        for f in values:
            if not isinstance(f, Finding):
                raise TypeError(f"Expected Finding instance, got {type(f).__name__}")
            if f.finding_id in seen_ids:
                raise ValueError(f"Duplicate finding_id '{f.finding_id}' in findings")
            seen_ids.add(f.finding_id)
        return values

    @field_validator("evidence")
    @classmethod
    def _validate_evidence(cls, values: list[Evidence]) -> list[Evidence]:
        seen_ids: set[str] = set()
        for ev in values:
            if not isinstance(ev, Evidence):
                raise TypeError(f"Expected Evidence instance, got {type(ev).__name__}")
            if ev.evidence_id in seen_ids:
                raise ValueError(f"Duplicate evidence_id '{ev.evidence_id}' in evidence")
            seen_ids.add(ev.evidence_id)
        return values

    @field_validator("code_investigation_results")
    @classmethod
    def _validate_code_investigation_results(
        cls, values: list[CodeInvestigationResult]
    ) -> list[CodeInvestigationResult]:
        seen_paths: set[str] = set()
        for res in values:
            if not isinstance(res, CodeInvestigationResult):
                raise TypeError(f"Expected CodeInvestigationResult, got {type(res).__name__}")
            if res.file_path in seen_paths:
                raise ValueError(
                    f"Duplicate file_path '{res.file_path}' in code_investigation_results"
                )
            seen_paths.add(res.file_path)
        return values

    @model_validator(mode="after")
    def _validate_bundle_integrity(self) -> "InvestigationResultBundle":
        # 1. Failure behavior: require meaningful errors for FAILED investigations
        if self.investigation.status == InvestigationStatus.FAILED:
            if not self.errors:
                raise ValueError(
                    "Investigation with status FAILED must include at least one error message in 'errors'"
                )

        # 2. Finding IDs integrity: check for duplicate finding_ids and exact list correspondence
        if len(self.investigation.finding_ids) != len(set(self.investigation.finding_ids)):
            raise ValueError(
                f"Duplicate finding_ids found in investigation.finding_ids: {self.investigation.finding_ids}"
            )

        finding_ids_in_list = [f.finding_id for f in self.findings]
        if self.investigation.finding_ids != finding_ids_in_list:
            if set(self.investigation.finding_ids) == set(finding_ids_in_list):
                raise ValueError(
                    f"Order mismatch between investigation.finding_ids ({self.investigation.finding_ids}) "
                    f"and findings list IDs ({finding_ids_in_list})"
                )
            raise ValueError(
                f"Mismatch between investigation.finding_ids ({self.investigation.finding_ids}) "
                f"and findings list IDs ({finding_ids_in_list})"
            )

        # 3. Evidence referential integrity: Every evidence_id in findings must exist in evidence
        available_evidence_ids = {ev.evidence_id for ev in self.evidence}
        for finding in self.findings:
            if len(finding.evidence_ids) != len(set(finding.evidence_ids)):
                raise ValueError(
                    f"Finding '{finding.finding_id}' contains duplicate evidence_ids: {finding.evidence_ids}"
                )
            for ev_id in finding.evidence_ids:
                if ev_id not in available_evidence_ids:
                    raise ValueError(
                        f"Finding '{finding.finding_id}' references unknown evidence_id '{ev_id}' "
                        f"not present in bundle evidence list"
                    )

        # 4. Traceability with evidence_build_result if provided
        if self.evidence_build_result is not None:
            build_result_ev_ids = {ev.evidence_id for ev in self.evidence_build_result.evidence}
            if not build_result_ev_ids.issubset(available_evidence_ids):
                missing = build_result_ev_ids - available_evidence_ids
                raise ValueError(
                    f"Bundle evidence list is missing evidence IDs from evidence_build_result: {missing}"
                )

        # 5. Traceability with test_evidence_build_result if provided
        if self.test_evidence_build_result is not None:
            test_ev_ids = {ev.evidence_id for ev in self.test_evidence_build_result.evidence}
            if not test_ev_ids.issubset(available_evidence_ids):
                missing = test_ev_ids - available_evidence_ids
                raise ValueError(
                    f"Bundle evidence list is missing evidence IDs from test_evidence_build_result: {missing}"
                )

        return self

    @property
    def investigation_id(self) -> str:
        return self.investigation.investigation_id

    @property
    def is_successful(self) -> bool:
        return self.investigation.status == InvestigationStatus.COMPLETED
