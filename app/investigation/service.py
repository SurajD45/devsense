"""
DevSense — Investigation Services
==================================
Deterministic services for the investigation pipeline stages.

All services in this module are provider-independent, pure-logic
implementations with no external dependencies (no LLM, no GitHub,
no Jira, no filesystem, no database).  Each is designed to be
easily replaceable by a future LLM-backed implementation that
preserves the same public API.
"""

from __future__ import annotations

import os
import re

from app.domain.models import (
    AcceptanceCriterion,
    ChangedFile,
    Evidence,
    EvidenceType,
    Requirement,
)
from app.investigation.schemas import (
    AnalyzedCriterion,
    ChangeImpactAnalysis,
    ChangedFileClassification,
    CodeEvidenceBuildResult,
    CodeInvestigationResult,
    CodeObservation,
    CodeObservationType,
    FileClassification,
    FilePromotionAnalysis,
    FileReviewItem,
    FileReviewPriority,
    RequirementAnalysis,
    RequirementAwareCodeInvestigationInput,
)


class RequirementAnalysisService:
    """
    Converts a domain Requirement into a RequirementAnalysis DTO.

    This deterministic implementation extracts structured analysis
    from the requirement's existing text without invoking any external
    services or AI models.  A future implementation may replace the
    internals with LLM-based reasoning while preserving the
    ``analyze(requirement) -> RequirementAnalysis`` contract.
    """

    def analyze(self, requirement: Requirement) -> RequirementAnalysis:
        """
        Analyze a Requirement and produce a RequirementAnalysis DTO.

        Parameters
        ----------
        requirement : Requirement
            A validated domain Requirement containing at least one
            AcceptanceCriterion.

        Returns
        -------
        RequirementAnalysis
            Structured operational breakdown of every acceptance criterion.

        Raises
        ------
        ValueError
            If the requirement has no acceptance criteria.
        """
        if not requirement.acceptance_criteria:
            raise ValueError(
                f"Requirement '{requirement.requirement_id}' has no acceptance criteria; "
                "cannot produce a RequirementAnalysis without at least one criterion"
            )

        summary = self._build_summary(requirement)
        criteria = [
            self._analyze_criterion(ac)
            for ac in requirement.acceptance_criteria
        ]

        return RequirementAnalysis(
            requirement_id=requirement.requirement_id,
            summary=summary,
            criteria=criteria,
        )

    # ------------------------------------------------------------------
    # Private helpers — deterministic text transformations
    # ------------------------------------------------------------------

    def _build_summary(self, requirement: Requirement) -> str:
        """
        Build a concise summary from the requirement's title and
        optional description.  Does not invent content.
        """
        if requirement.description:
            return (
                f"Requirement analysis: {requirement.title}. "
                f"{requirement.description}"
            )
        return f"Requirement analysis: {requirement.title}."

    def _analyze_criterion(self, ac: AcceptanceCriterion) -> AnalyzedCriterion:
        """
        Convert a single AcceptanceCriterion into an AnalyzedCriterion
        using only the criterion's existing text.
        """
        interpretation = self._build_interpretation(ac)
        verification_points = self._build_verification_points(ac)

        return AnalyzedCriterion(
            criterion_id=ac.criterion_id,
            interpretation=interpretation,
            verification_points=verification_points,
            potential_areas=[],
        )

    def _build_interpretation(self, ac: AcceptanceCriterion) -> str:
        """
        Produce a deterministic interpretation by combining the
        criterion's title with its description (when present).
        """
        if ac.description:
            return f"{ac.title}: {ac.description}"
        return ac.title

    def _build_verification_points(self, ac: AcceptanceCriterion) -> list[str]:
        """
        Extract verification points from the criterion's text.

        The baseline deterministic strategy produces a single
        verification point derived from the criterion title.
        A future LLM-backed implementation can decompose the
        criterion into finer-grained points.
        """
        points = [f"Verify: {ac.title}"]

        if ac.description:
            points.append(f"Verify: {ac.description}")

        return points


# ---------------------------------------------------------------------------
# Test-file patterns
# ---------------------------------------------------------------------------

_TEST_DIR_PATTERN = re.compile(r"(^|/)tests?/", re.IGNORECASE)
_TEST_FILE_PATTERN = re.compile(r"(^|/)test_[^/]+$", re.IGNORECASE)
_SPEC_FILE_PATTERN = re.compile(r"\.(spec|test)\.", re.IGNORECASE)

# Documentation extensions
_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}

# Configuration / infrastructure extensions
_CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env",
}

# Migration patterns
_MIGRATION_PATTERN = re.compile(r"(^|/)migrations?/", re.IGNORECASE)


def _is_test_file(path: str) -> bool:
    """Return True if the file path looks like a test file."""
    return bool(
        _TEST_DIR_PATTERN.search(path)
        or _TEST_FILE_PATTERN.search(path)
        or _SPEC_FILE_PATTERN.search(path)
    )


def _is_doc_file(path: str) -> bool:
    """Return True if the file path looks like documentation."""
    _, ext = os.path.splitext(path)
    return ext.lower() in _DOC_EXTENSIONS


def _is_config_file(path: str) -> bool:
    """Return True if the file path looks like a configuration file."""
    _, ext = os.path.splitext(path)
    return ext.lower() in _CONFIG_EXTENSIONS


def _is_migration_file(path: str) -> bool:
    """Return True if the file path is inside a migrations directory."""
    return bool(_MIGRATION_PATTERN.search(path))


def _extract_path_terms(path: str) -> set[str]:
    """
    Extract meaningful terms from a file path for keyword matching.

    Splits on path separators, dots, underscores, and hyphens,
    then normalizes to lowercase.  Discards very short tokens
    (< 3 chars) to avoid false positives on common fragments.
    """
    # Strip extension first
    base, _ = os.path.splitext(path)
    parts = re.split(r"[/\\._\-]+", base)
    return {p.lower() for p in parts if len(p) >= 3}


_IGNORE_TERMS = {
    "requirement", "requirements", "analysis", "verify", "verification",
    "criterion", "criteria", "the", "and", "for", "with", "that", "this",
    "from", "into", "add", "added", "adding",
}


def _extract_analysis_terms(analysis: RequirementAnalysis) -> set[str]:
    """
    Collect lowercase keyword terms from the RequirementAnalysis
    summary, criterion interpretations, and verification points.
    """
    terms: set[str] = set()
    for word in re.split(r"\W+", analysis.summary):
        w = word.lower()
        if len(w) >= 3 and w not in _IGNORE_TERMS:
            terms.add(w)
    for criterion in analysis.criteria:
        for word in re.split(r"\W+", criterion.interpretation):
            w = word.lower()
            if len(w) >= 3 and w not in _IGNORE_TERMS:
                terms.add(w)
        for point in criterion.verification_points:
            for word in re.split(r"\W+", point):
                w = word.lower()
                if len(w) >= 3 and w not in _IGNORE_TERMS:
                    terms.add(w)
    return terms


def _source_path_for_test(test_path: str) -> str | None:
    """
    Given a test file path, derive the likely source file path
    it is testing.  Returns None if no derivation is possible.

    Example:  tests/domain/test_models.py  →  domain/models.py
    """
    # Strip leading test directory
    cleaned = re.sub(r"^tests?/", "", test_path, flags=re.IGNORECASE)
    # Strip test_ prefix from filename
    dirname = os.path.dirname(cleaned)
    basename = os.path.basename(cleaned)
    source_name = re.sub(r"^test_", "", basename, flags=re.IGNORECASE)
    if source_name == basename:
        return None  # couldn't strip prefix
    if dirname:
        return f"{dirname}/{source_name}"
    return source_name


class ChangeImpactAnalysisService:
    """
    Classifies PR changed files against a RequirementAnalysis.

    This deterministic implementation uses conservative keyword
    matching and structural heuristics.  A future LLM-backed
    implementation can replace the internals while preserving
    the ``analyze(analysis, changed_files) → ChangeImpactAnalysis``
    contract.

    Core invariant:
        Every input ChangedFile appears exactly once in the output.
        Files that cannot be confidently classified as PRIMARY
        are classified as SECONDARY — never discarded.
    """

    def analyze(
        self,
        analysis: RequirementAnalysis,
        changed_files: list[ChangedFile],
    ) -> ChangeImpactAnalysis:
        """
        Classify every changed file relative to the requirement analysis.

        Parameters
        ----------
        analysis : RequirementAnalysis
            Output of the requirement analysis stage.
        changed_files : list[ChangedFile]
            All files changed in the pull request.

        Returns
        -------
        ChangeImpactAnalysis
            One classification entry per changed file.

        Raises
        ------
        ValueError
            If changed_files is empty.
        """
        if not changed_files:
            raise ValueError(
                "changed_files must contain at least one file; "
                "cannot perform impact analysis on an empty changeset"
            )

        analysis_terms = _extract_analysis_terms(analysis)

        # Build set of primary source paths for test-file association
        primary_source_paths: set[str] = set()
        classifications: list[ChangedFileClassification] = []

        # First pass: classify non-test files
        for cf in changed_files:
            if _is_test_file(cf.path):
                continue  # handled in second pass
            classification, reasons = self._classify_source_file(
                cf.path, analysis_terms,
            )
            if classification == FileClassification.PRIMARY:
                primary_source_paths.add(cf.path)
            classifications.append(
                ChangedFileClassification(
                    file_path=cf.path,
                    classification=classification,
                    reasons=reasons,
                )
            )

        # Second pass: classify test files
        for cf in changed_files:
            if not _is_test_file(cf.path):
                continue
            classification, reasons = self._classify_test_file(
                cf.path, analysis_terms, primary_source_paths, changed_files,
            )
            classifications.append(
                ChangedFileClassification(
                    file_path=cf.path,
                    classification=classification,
                    reasons=reasons,
                )
            )

        return ChangeImpactAnalysis(classifications=classifications)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_source_file(
        self,
        path: str,
        analysis_terms: set[str],
    ) -> tuple[FileClassification, list[str]]:
        """
        Classify a non-test changed file.

        Returns (classification, reasons).
        """
        reasons: list[str] = []

        # Supporting file types get SECONDARY with a descriptive reason
        if _is_doc_file(path):
            reasons.append("Documentation file included in changeset")
            return FileClassification.SECONDARY, reasons

        if _is_config_file(path):
            reasons.append("Configuration file included in changeset")
            return FileClassification.SECONDARY, reasons

        if _is_migration_file(path):
            reasons.append("Migration file included in changeset")
            return FileClassification.SECONDARY, reasons

        path_terms = _extract_path_terms(path)
        matches = path_terms & analysis_terms

        if matches:
            reasons.append(
                f"Path terms match requirement analysis: {', '.join(sorted(matches))}"
            )
            return FileClassification.PRIMARY, reasons

        # Default: SECONDARY with honest reason
        reasons.append(
            "No direct keyword match to requirement analysis; "
            "retained for comprehensive review"
        )
        return FileClassification.SECONDARY, reasons

    def _classify_test_file(
        self,
        path: str,
        analysis_terms: set[str],
        primary_source_paths: set[str],
        changed_files: list[ChangedFile],
    ) -> tuple[FileClassification, list[str]]:
        """
        Classify a test file.

        A test file is PRIMARY if:
        - its inferred source file is among the primary source paths, or
        - its path terms match the requirement analysis terms.

        Returns (classification, reasons).
        """
        reasons: list[str] = []

        # Check if test file is associated with a primary source file
        source_path = _source_path_for_test(path)
        if source_path:
            changed_paths = {cf.path for cf in changed_files}
            # Check both exact match and suffix match against primary sources
            for primary in primary_source_paths:
                if primary.endswith(source_path) or source_path == primary:
                    reasons.append(
                        f"Test file associated with primary source: {primary}"
                    )
                    return FileClassification.PRIMARY, reasons

        # Direct keyword match
        path_terms = _extract_path_terms(path)
        matches = path_terms & analysis_terms
        if matches:
            reasons.append(
                f"Test path terms match requirement analysis: {', '.join(sorted(matches))}"
            )
            return FileClassification.PRIMARY, reasons

        reasons.append(
            "Test file without direct association to primary sources; "
            "retained for comprehensive review"
        )
        return FileClassification.SECONDARY, reasons

    def promote_secondary_files(
        self,
        impact_analysis: ChangeImpactAnalysis,
        changed_files: list[ChangedFile],
        large_change_threshold: int = 100,
    ) -> FilePromotionAnalysis:
        """
        Screen SECONDARY files from a ChangeImpactAnalysis and promote those
        that warrant deeper investigation based on deterministic signals.
        """
        service = SuspiciousFilePromotionService(
            large_change_threshold=large_change_threshold
        )
        return service.promote(impact_analysis, changed_files)


# ---------------------------------------------------------------------------
# Promotion Signals Patterns & Constants
# ---------------------------------------------------------------------------

# 1. Security-sensitive paths/files
_SECURITY_PATH_PATTERN = re.compile(
    r"(^|/|_|-)(auth|authentication|authorization|security|permission|permissions|access_control|access-control|credential|credentials|secret|secrets|token|tokens|rbac|acl|oauth|jwt|session|sessions|password|passwords)($|/|_|-|\.)",
    re.IGNORECASE,
)
_SECURITY_MIDDLEWARE_PATTERN = re.compile(
    r"middleware.*(auth|security|access|token|permission)|(auth|security|access|token|permission).*middleware",
    re.IGNORECASE,
)

# 2. Database/schema changes
_MIGRATION_OR_SCHEMA_PATTERN = re.compile(
    r"(^|/)(migrations?|schemas?|alembic|flyway|liquibase)/",
    re.IGNORECASE,
)
_DB_SCHEMA_FILE_PATTERN = re.compile(
    r"(^|/|_|-)(migration|migrations|schema|schemas|database|db_config|db_schema)($|/|_|-|\.)",
    re.IGNORECASE,
)
_SQL_EXT_PATTERN = re.compile(r"\.sql$", re.IGNORECASE)

# 3. Dependency changes
_KNOWN_DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "poetry.lock",
    "uv.lock",
    "pipfile",
    "pipfile.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "pom.xml",
    "build.gradle",
    "setup.py",
    "setup.cfg",
}
_DEPENDENCY_PATTERN = re.compile(
    r"(^|/)(requirements.*\.txt|deps.*\.txt)$",
    re.IGNORECASE,
)

# 4. CI/CD or deployment changes
_CICD_DEPLOYMENT_PATTERNS = [
    re.compile(r"^\.github/(workflows|actions)/", re.IGNORECASE),
    re.compile(r"^\.(gitlab-ci|circleci)/", re.IGNORECASE),
    re.compile(r"(^|/)Dockerfile(\..*)?$", re.IGNORECASE),
    re.compile(r"(^|/)docker-compose(\..*)?\.ya?ml$", re.IGNORECASE),
    re.compile(r"(^|/)(deploy|deployment|deployments|k8s|kubernetes|helm|terraform)/", re.IGNORECASE),
    re.compile(r"(^|/)(Procfile|serverless\.ya?ml|app\.ya?ml)$", re.IGNORECASE),
]

# 5. Application-boundary & runtime configuration
_BOUNDARY_DIR_PATTERN = re.compile(
    r"(^|/)(routes|routing|router|routers|endpoints|controllers|handlers|api)/",
    re.IGNORECASE,
)
_BOUNDARY_FILE_PATTERN = re.compile(
    r"(^|/|_|-)(route|router|routes|controller|controllers|handler|handlers|endpoint|endpoints)($|_|-|\.)",
    re.IGNORECASE,
)
_RUNTIME_CONFIG_PATTERN = re.compile(
    r"(^|/)(settings|config|configuration)(\.py|\.ya?ml|\.json|\.toml|\.env|$|/)",
    re.IGNORECASE,
)
_GATEWAY_MIDDLEWARE_PATTERN = re.compile(
    r"(^|/)(gateway|proxy|middleware)/",
    re.IGNORECASE,
)


def _detect_security_signal(path: str) -> str | None:
    if _SECURITY_PATH_PATTERN.search(path) or _SECURITY_MIDDLEWARE_PATTERN.search(path):
        return "Security-sensitive path or access-control file detected"
    return None


def _detect_database_schema_signal(path: str) -> str | None:
    if (
        _MIGRATION_OR_SCHEMA_PATTERN.search(path)
        or _DB_SCHEMA_FILE_PATTERN.search(path)
        or _SQL_EXT_PATTERN.search(path)
    ):
        return "Database or schema modification detected"
    return None


def _detect_dependency_signal(path: str) -> str | None:
    basename = os.path.basename(path).lower()
    if basename in _KNOWN_DEPENDENCY_FILES or _DEPENDENCY_PATTERN.search(path):
        return f"Dependency configuration file detected ({basename})"
    return None


def _detect_cicd_signal(path: str) -> str | None:
    for pattern in _CICD_DEPLOYMENT_PATTERNS:
        if pattern.search(path):
            return "CI/CD or deployment configuration detected"
    return None


def _detect_boundary_signal(path: str) -> str | None:
    if (
        _BOUNDARY_DIR_PATTERN.search(path)
        or _BOUNDARY_FILE_PATTERN.search(path)
        or _RUNTIME_CONFIG_PATTERN.search(path)
        or _GATEWAY_MIDDLEWARE_PATTERN.search(path)
    ):
        return "Application boundary or runtime configuration file detected"
    return None


def _detect_large_change_signal(
    changed_file: ChangedFile,
    threshold: int,
) -> str | None:
    total = changed_file.additions + changed_file.deletions
    if total >= threshold:
        return (
            f"Large changeset detected: {total} lines modified "
            f"({changed_file.additions} additions, {changed_file.deletions} deletions; "
            f"threshold: {threshold})"
        )
    return None


# ---------------------------------------------------------------------------
# SuspiciousFilePromotionService
# ---------------------------------------------------------------------------

class SuspiciousFilePromotionService:
    """
    Screens SECONDARY files from a ChangeImpactAnalysis and deterministically
    promotes files that warrant deeper investigation.

    Core Invariants:
    1. Existing PRIMARY files remain PRIMARY (never downgraded or promoted).
    2. Existing ordinary SECONDARY files remain SECONDARY unless a promotion signal matches.
    3. Only SECONDARY files can be promoted.
    4. A promoted file retains original_classification=FileClassification.SECONDARY.
    5. Every changed file in the changeset appears exactly once in the output.
    6. PROMOTED does NOT mean the file is buggy, unsafe, or violates the requirement;
       it means only that 'This file deserves deeper investigation.'
    7. No findings, AI/LLM calls, or database operations are produced.
    """

    DEFAULT_LARGE_CHANGE_THRESHOLD: int = 100

    def __init__(self, large_change_threshold: int = DEFAULT_LARGE_CHANGE_THRESHOLD) -> None:
        if large_change_threshold <= 0:
            raise ValueError("large_change_threshold must be greater than zero")
        self.large_change_threshold = large_change_threshold

    def promote(
        self,
        impact_analysis: ChangeImpactAnalysis,
        changed_files: list[ChangedFile],
    ) -> FilePromotionAnalysis:
        """
        Evaluate all files in impact_analysis against deterministic promotion signals.

        Parameters
        ----------
        impact_analysis : ChangeImpactAnalysis
            Classifications produced by ChangeImpactAnalysisService.
        changed_files : list[ChangedFile]
            The full list of changed files with additions/deletions metadata.

        Returns
        -------
        FilePromotionAnalysis
            A comprehensive review plan with priorities: PRIMARY, SECONDARY, or PROMOTED.

        Raises
        ------
        ValueError
            If inputs are empty or there is a file path mismatch between the two inputs.
        """
        if not impact_analysis.classifications:
            raise ValueError(
                "impact_analysis must contain at least one classification"
            )
        if not changed_files:
            raise ValueError(
                "changed_files must contain at least one changed file"
            )

        changed_files_by_path: dict[str, ChangedFile] = {}
        for cf in changed_files:
            if cf.path in changed_files_by_path:
                raise ValueError(
                    f"Duplicate file path '{cf.path}' in changed_files"
                )
            changed_files_by_path[cf.path] = cf

        classification_paths = {c.file_path for c in impact_analysis.classifications}
        input_paths = set(changed_files_by_path.keys())

        missing_in_files = classification_paths - input_paths
        if missing_in_files:
            raise ValueError(
                f"Files in impact_analysis not found in changed_files: {sorted(missing_in_files)}"
            )

        missing_in_analysis = input_paths - classification_paths
        if missing_in_analysis:
            raise ValueError(
                f"Files in changed_files not found in impact_analysis: {sorted(missing_in_analysis)}"
            )

        review_items: list[FileReviewItem] = []

        for classification in impact_analysis.classifications:
            cf = changed_files_by_path[classification.file_path]

            # Invariant 1: PRIMARY files remain PRIMARY
            if classification.classification == FileClassification.PRIMARY:
                review_items.append(
                    FileReviewItem(
                        file_path=classification.file_path,
                        priority=FileReviewPriority.PRIMARY,
                        reasons=list(classification.reasons),
                        original_classification=FileClassification.PRIMARY,
                    )
                )
                continue

            # Invariant 2 & 3: Screen SECONDARY files for promotion
            signals: list[str] = []

            # 1. Security-sensitive
            sig = _detect_security_signal(cf.path)
            if sig:
                signals.append(sig)

            # 2. Database/schema
            sig = _detect_database_schema_signal(cf.path)
            if sig:
                signals.append(sig)

            # 3. Dependencies
            sig = _detect_dependency_signal(cf.path)
            if sig:
                signals.append(sig)

            # 4. CI/CD & deployment
            sig = _detect_cicd_signal(cf.path)
            if sig:
                signals.append(sig)

            # 5. Large changes
            sig = _detect_large_change_signal(cf, self.large_change_threshold)
            if sig:
                signals.append(sig)

            # 6. Boundary & runtime config
            sig = _detect_boundary_signal(cf.path)
            if sig:
                signals.append(sig)

            if signals:
                promotion_reasons = list(signals)
                promotion_reasons.append("Requires deeper investigation")
                review_items.append(
                    FileReviewItem(
                        file_path=classification.file_path,
                        priority=FileReviewPriority.PROMOTED,
                        reasons=promotion_reasons,
                        original_classification=FileClassification.SECONDARY,
                    )
                )
            else:
                # Ordinary secondary file remains SECONDARY
                review_items.append(
                    FileReviewItem(
                        file_path=classification.file_path,
                        priority=FileReviewPriority.SECONDARY,
                        reasons=list(classification.reasons),
                        original_classification=FileClassification.SECONDARY,
                    )
                )

        return FilePromotionAnalysis(files=review_items)

    def analyze(
        self,
        impact_analysis: ChangeImpactAnalysis,
        changed_files: list[ChangedFile],
    ) -> FilePromotionAnalysis:
        """Alias for promote()."""
        return self.promote(impact_analysis, changed_files)


FilePromotionService = SuspiciousFilePromotionService


# ---------------------------------------------------------------------------
# Code Investigation Patterns
# ---------------------------------------------------------------------------

_TODO_PATTERN = re.compile(
    r"\b(TODO|FIXME)\b(?:\s*[:\-])?\s*(.*)",
    re.IGNORECASE,
)
_BARE_EXCEPT_PATTERN = re.compile(
    r"^\s*except\s*:\s*(?:#.*)?$",
)
_DEBUG_PRINT_PATTERN = re.compile(
    r"^\s*(?:console\.(?:log|debug|info|warn|error)|print|System\.out\.print(?:ln)?|fmt\.Print(?:ln|f)?)\s*\(",
)
_CREDENTIAL_KEYWORD_PATTERN = re.compile(
    r"""(?i)\b(api_key|apikey|secret_key|private_key|auth_token|access_token|client_secret|password|passwd)\s*=\s*['"]([A-Za-z0-9_\-\.]{8,})['"]"""
)
_TOKEN_LITERAL_PATTERN = re.compile(
    r"""\b(ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|bearer\s+[A-Za-z0-9_\-\.]{20,})\b""",
    re.IGNORECASE,
)
_ENCLOSING_DEF_PATTERN = re.compile(
    r"^\s*(?:async\s+)?(?:def|function)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
)
_ENCLOSING_CLASS_PATTERN = re.compile(
    r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b",
)

# Stopwords to avoid false positive relevance correlations
_RELEVANCE_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "once", "here", "there", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "should", "now", "must", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "doing",
    "would", "could", "shall", "may", "might", "verify", "verification",
    "point", "points", "criterion", "criteria", "test", "check", "ensure",
    "observed", "marker", "potential", "appears", "occurs", "occur",
    "while", "following", "code", "file", "line", "lines", "statement",
    "handler", "src", "app", "lib", "main", "this", "that", "these", "those",
    "of", "done", "which", "its", "it",
}


def _tokenize(text: str | None) -> set[str]:
    """
    Extract lowercase domain terms from text, handling camelCase,
    snake_case, and path separators, while discarding short tokens and stopwords.
    """
    if not text:
        return set()
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[A-Za-z0-9]+", expanded)
    tokens = set()
    for w in words:
        cleaned = w.lower()
        if len(cleaned) >= 3 and cleaned not in _RELEVANCE_STOPWORDS:
            tokens.add(cleaned)
    return tokens


def _compute_criterion_terms(
    criterion: AnalyzedCriterion,
    analysis: RequirementAnalysis,
) -> set[str]:
    """
    Collect domain terms representing an acceptance criterion from its
    interpretation, verification points, and potential areas, supplemented
    by requirement summary context.
    """
    terms = set()
    terms |= _tokenize(criterion.interpretation)
    for vp in criterion.verification_points:
        terms |= _tokenize(vp)
    for pa in criterion.potential_areas:
        terms |= _tokenize(pa)

    summary_terms = _tokenize(analysis.summary)
    if len(analysis.criteria) == 1:
        terms |= summary_terms
    else:
        if terms & summary_terms:
            terms |= summary_terms

    return terms


def _match_criteria_for_observation(
    file_path: str,
    symbol: str | None,
    description: str,
    line: str | None,
    analysis: RequirementAnalysis | None,
) -> list[str]:
    """
    Determine which acceptance criteria (if any) are relevant to the observation.

    Returns a list of criterion_id strings ordered by relevance score descending,
    with tie-breaking on criterion_id ascending.
    """
    if not analysis or not analysis.criteria:
        return []

    obs_terms = set()
    obs_terms |= _tokenize(file_path)
    if symbol:
        obs_terms |= _tokenize(symbol)
    obs_terms |= _tokenize(description)
    if line:
        obs_terms |= _tokenize(line)

    if not obs_terms:
        return []

    matched_criteria: list[tuple[int, str]] = []

    for ac in analysis.criteria:
        crit_terms = _compute_criterion_terms(ac, analysis)
        matches = obs_terms & crit_terms
        if matches:
            matched_criteria.append((len(matches), ac.criterion_id))

    if not matched_criteria:
        return []

    matched_criteria.sort(key=lambda item: (-item[0], item[1]))
    return [cid for _, cid in matched_criteria]


# ---------------------------------------------------------------------------
# CodeInvestigationService
# ---------------------------------------------------------------------------

class CodeInvestigationService:
    """
    Deterministic code investigation service.

    Examines selected changed-file content and records structured, factual observations
    (such as TODO/FIXME markers, bare excepts, debug prints, potential credential patterns).
    When a RequirementAnalysis is provided, observations are correlated with relevant
    acceptance-criterion verification points.

    Invariants:
    1. Observations are NOT findings — they record concrete code characteristics without
       making verification conclusions or asserting criterion satisfaction.
    2. No LLM, no AI reasoning, no external GitHub/Jira calls, no database access.
    3. Fully deterministic output with consistent observation IDs (CODE-1, CODE-2, ...).
    4. Unrelated observations are preserved without criterion associations.
    """

    DEFAULT_LARGE_FILE_THRESHOLD: int = 500

    def __init__(self, large_file_threshold: int = DEFAULT_LARGE_FILE_THRESHOLD) -> None:
        if large_file_threshold <= 0:
            raise ValueError("large_file_threshold must be greater than zero")
        self.large_file_threshold = large_file_threshold

    def investigate(
        self,
        file_path: str,
        content: str,
        requirement_analysis: RequirementAnalysis | None = None,
        review_priority: FileReviewPriority | None = None,
    ) -> CodeInvestigationResult:
        """
        Analyze a file's content and produce structured code observations, optionally
        associating observations with relevant criteria from a RequirementAnalysis.

        Parameters
        ----------
        file_path : str
            The path of the file being investigated.
        content : str
            The raw text content of the file.
        requirement_analysis : RequirementAnalysis, optional
            Requirement analysis context for criterion correlation.
        review_priority : FileReviewPriority, optional
            Review priority assigned during prior pipeline stages.

        Returns
        -------
        CodeInvestigationResult
            Result containing observations recorded for this file.

        Raises
        ------
        ValueError
            If file_path is empty or whitespace-only.
        """
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path cannot be empty or whitespace-only")
        cleaned_path = file_path.strip()

        if not isinstance(content, str) or not content.strip():
            return CodeInvestigationResult(file_path=cleaned_path, observations=[])

        lines = content.splitlines()
        observations: list[CodeObservation] = []
        counter = 1
        current_symbol: str | None = None

        # Check for large file
        if len(lines) >= self.large_file_threshold:
            large_file_desc = f"Large source file observed: {len(lines)} lines (threshold: {self.large_file_threshold})"
            large_file_criteria = _match_criteria_for_observation(
                cleaned_path, None, large_file_desc, None, requirement_analysis
            )
            observations.append(
                CodeObservation(
                    observation_id=f"CODE-{counter}",
                    file_path=cleaned_path,
                    description=large_file_desc,
                    symbol=None,
                    start_line=1,
                    end_line=len(lines),
                    observation_type=CodeObservationType.CONFIGURATION,
                    criterion_ids=large_file_criteria,
                )
            )
            counter += 1

        for line_no, line in enumerate(lines, start=1):
            # Track current symbol (function / class definition)
            def_match = _ENCLOSING_DEF_PATTERN.search(line)
            if def_match:
                current_symbol = def_match.group(1)
            else:
                class_match = _ENCLOSING_CLASS_PATTERN.search(line)
                if class_match:
                    current_symbol = class_match.group(1)

            # 1. Conservative hard-coded credentials / secrets
            if _CREDENTIAL_KEYWORD_PATTERN.search(line) or _TOKEN_LITERAL_PATTERN.search(line):
                desc = "Potential hard-coded credential pattern observed"
                matched_crit = _match_criteria_for_observation(
                    cleaned_path, current_symbol, desc, line, requirement_analysis
                )
                observations.append(
                    CodeObservation(
                        observation_id=f"CODE-{counter}",
                        file_path=cleaned_path,
                        description=desc,
                        symbol=current_symbol,
                        start_line=line_no,
                        end_line=line_no,
                        observation_type=CodeObservationType.SECURITY,
                        criterion_ids=matched_crit,
                    )
                )
                counter += 1

            # 2. Bare exception swallowing
            if _BARE_EXCEPT_PATTERN.search(line):
                desc = "Bare exception handler observed; potential exception swallowing"
                matched_crit = _match_criteria_for_observation(
                    cleaned_path, current_symbol, desc, line, requirement_analysis
                )
                observations.append(
                    CodeObservation(
                        observation_id=f"CODE-{counter}",
                        file_path=cleaned_path,
                        description=desc,
                        symbol=current_symbol,
                        start_line=line_no,
                        end_line=line_no,
                        observation_type=CodeObservationType.ERROR_HANDLING,
                        criterion_ids=matched_crit,
                    )
                )
                counter += 1

            # 3. Debug print / console log
            if _DEBUG_PRINT_PATTERN.search(line):
                desc = "Debug print or console logging statement observed"
                matched_crit = _match_criteria_for_observation(
                    cleaned_path, current_symbol, desc, line, requirement_analysis
                )
                observations.append(
                    CodeObservation(
                        observation_id=f"CODE-{counter}",
                        file_path=cleaned_path,
                        description=desc,
                        symbol=current_symbol,
                        start_line=line_no,
                        end_line=line_no,
                        observation_type=CodeObservationType.CONTROL_FLOW,
                        criterion_ids=matched_crit,
                    )
                )
                counter += 1

            # 4. TODO / FIXME markers
            todo_match = _TODO_PATTERN.search(line)
            if todo_match:
                marker = todo_match.group(1).upper()
                detail = todo_match.group(2).strip()
                desc = f"{marker} marker observed"
                if detail:
                    desc += f": {detail}"
                matched_crit = _match_criteria_for_observation(
                    cleaned_path, current_symbol, desc, line, requirement_analysis
                )
                observations.append(
                    CodeObservation(
                        observation_id=f"CODE-{counter}",
                        file_path=cleaned_path,
                        description=desc,
                        symbol=current_symbol,
                        start_line=line_no,
                        end_line=line_no,
                        observation_type=CodeObservationType.LOGIC,
                        criterion_ids=matched_crit,
                    )
                )
                counter += 1

        return CodeInvestigationResult(
            file_path=cleaned_path,
            observations=observations,
        )

    def investigate_with_requirements(
        self,
        input_data: RequirementAwareCodeInvestigationInput,
    ) -> CodeInvestigationResult:
        """
        Investigate a file within the context of a RequirementAnalysis.
        """
        return self.investigate(
            file_path=input_data.file_path,
            content=input_data.file_content,
            requirement_analysis=input_data.requirement_analysis,
            review_priority=input_data.review_priority,
        )

    def correlate_observation(
        self,
        observation: CodeObservation,
        requirement_analysis: RequirementAnalysis,
        content: str = "",
    ) -> CodeObservation:
        """
        Correlate an existing CodeObservation with a RequirementAnalysis,
        populating its criterion_ids based on deterministic lexical matching.
        """
        lines = content.splitlines() if content else []
        line_content = ""
        if observation.start_line and 1 <= observation.start_line <= len(lines):
            line_content = lines[observation.start_line - 1]

        matched_ids = _match_criteria_for_observation(
            file_path=observation.file_path,
            symbol=observation.symbol,
            description=observation.description,
            line=line_content,
            analysis=requirement_analysis,
        )

        return CodeObservation(
            observation_id=observation.observation_id,
            file_path=observation.file_path,
            description=observation.description,
            symbol=observation.symbol,
            start_line=observation.start_line,
            end_line=observation.end_line,
            observation_type=observation.observation_type,
            criterion_ids=matched_ids,
        )


# ---------------------------------------------------------------------------
# RequirementAwareCodeInvestigationService
# ---------------------------------------------------------------------------

class RequirementAwareCodeInvestigationService:
    """
    Requirement-aware code investigation service.

    Connects RequirementAnalysis with Code Investigation so that code observations
    can be associated with relevant acceptance criteria.

    Invariants:
    1. Does NOT decide whether an acceptance criterion is verified or satisfied.
    2. Does NOT produce Findings, FindingStatus (VERIFIED, etc.), or final recommendations.
    3. Unrelated observations are preserved without criterion associations.
    4. Deterministic lexical matching with no LLM, external APIs, or database.
    """

    def __init__(self, investigator: CodeInvestigationService | None = None) -> None:
        self.investigator = investigator or CodeInvestigationService()

    def investigate(
        self,
        input_data: RequirementAwareCodeInvestigationInput,
    ) -> CodeInvestigationResult:
        """Analyze a changed file against requirement criteria."""
        return self.investigator.investigate_with_requirements(input_data)

    def correlate_observation(
        self,
        observation: CodeObservation,
        requirement_analysis: RequirementAnalysis,
        content: str = "",
    ) -> CodeObservation:
        """Correlate a single observation with requirement criteria."""
        return self.investigator.correlate_observation(
            observation, requirement_analysis, content
        )


# ---------------------------------------------------------------------------
# CodeEvidenceBuilder
# ---------------------------------------------------------------------------

class CodeEvidenceBuilder:
    """
    Builds domain Evidence objects from CodeObservations.

    Converts concrete code investigation observations into Evidence domain objects
    suitable for downstream citation and investigation analysis.

    Architectural Invariants:
    1. Evidence is a reference to an artifact, not stored source content.
    2. Does NOT store full code, patches, or file contents inside Evidence.
    3. Uses the existing domain Evidence model (EvidenceType.CODE).
    4. Does NOT create Findings.
    5. Does NOT decide whether an acceptance criterion is verified.
    6. Does NOT assign verification statuses (VERIFIED, etc.).
    7. Pure deterministic logic (no LLM, no external APIs, no DB).
    8. Preserves traceability via CodeEvidenceBuildResult (observation_id -> evidence_id).
    """

    def build(
        self,
        observations: list[CodeObservation],
        repository: str | None = None,
        commit_sha: str | None = None,
        start_id: int = 1,
    ) -> CodeEvidenceBuildResult:
        """
        Convert code observations into domain Evidence objects.

        Parameters
        ----------
        observations : list[CodeObservation]
            List of CodeObservation instances to convert.
        repository : str | None, optional
            Repository identifier to associate with the generated Evidence.
        commit_sha : str | None, optional
            Git commit SHA to associate with the generated Evidence.
        start_id : int, optional
            Starting sequence number for deterministic Evidence IDs (default 1 -> EV-1).

        Returns
        -------
        CodeEvidenceBuildResult
            Result containing the list of Evidence objects and the
            observation_id -> evidence_id mapping for traceability.
        """
        if not isinstance(observations, list):
            raise TypeError(
                f"observations must be a list, got {type(observations).__name__}"
            )

        if start_id < 1:
            raise ValueError(f"start_id must be >= 1, got {start_id}")

        evidence_list: list[Evidence] = []
        mapping: dict[str, str] = {}
        seen_obs_ids: set[str] = set()

        for idx, obs in enumerate(observations):
            if not isinstance(obs, CodeObservation):
                raise TypeError(
                    f"All items in observations must be CodeObservation instances; "
                    f"item at index {idx} is {type(obs).__name__}"
                )

            if obs.observation_id in seen_obs_ids:
                raise ValueError(
                    f"Duplicate observation_id '{obs.observation_id}' found in observations"
                )
            seen_obs_ids.add(obs.observation_id)

            ev_id = f"EV-{start_id + idx}"
            evidence = Evidence(
                evidence_id=ev_id,
                evidence_type=EvidenceType.CODE,
                repository=repository,
                file_path=obs.file_path,
                start_line=obs.start_line,
                end_line=obs.end_line,
                symbol=obs.symbol,
                commit_sha=commit_sha,
                description=obs.description,
            )
            evidence_list.append(evidence)
            mapping[obs.observation_id] = ev_id

        return CodeEvidenceBuildResult(
            evidence=evidence_list,
            observation_to_evidence=mapping,
        )

    build_result = build

    def build_evidence(
        self,
        observations: list[CodeObservation],
        repository: str | None = None,
        commit_sha: str | None = None,
        start_id: int = 1,
    ) -> list[Evidence]:
        """
        Convenience method returning directly the list of Evidence objects.
        """
        return self.build(
            observations=observations,
            repository=repository,
            commit_sha=commit_sha,
            start_id=start_id,
        ).evidence


EvidenceBuilder = CodeEvidenceBuilder




