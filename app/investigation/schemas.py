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

from app.domain.models import Evidence

# ---------------------------------------------------------------------------
# Validation Helpers & Patterns
# ---------------------------------------------------------------------------

_CRITERION_ID_PATTERN = re.compile(r"^AC-[0-9]+$")
_EVIDENCE_ID_PATTERN = re.compile(r"^EV-[0-9]+$")
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




