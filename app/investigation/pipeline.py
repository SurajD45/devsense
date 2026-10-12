"""
DevSense — Investigation Pipeline Orchestrator
==============================================
Deterministic orchestration layer connecting modular investigation services
into an end-to-end requirement verification workflow.

Pipeline Flow:
--------------
1. Accept and validate InvestigationPipelineInput.
2. Establish deterministic investigation ID (e.g., 'INV-<pr_number>').
3. Stage 1: Requirement Analysis (RequirementAnalysisService).
4. Stage 2: Change Impact Analysis (ChangeImpactAnalysisService).
5. Stage 3: Suspicious File Promotion (SuspiciousFilePromotionService).
6. Stage 4: Code Investigation on Primary & Promoted files (RequirementAwareCodeInvestigationService).
7. Stage 5: Concrete Evidence Building (CodeEvidenceBuilder).
8. Stage 6: Finding Synthesis (FindingSynthesisService).
9. Stage 7: Bundle Assembly & Invariant Validation (InvestigationResultBundle).

Security & Invariants:
----------------------
- Deterministic execution without external API, LLM, or database dependencies.
- Strict referential integrity between findings and concrete evidence.
- Findings without supporting evidence are never marked VERIFIED.
- All intermediate artifacts are retained for full auditability and explainability.
"""

from collections import defaultdict
from datetime import datetime, timezone
import logging
import re
from typing import Any

from app.domain.models import (
    Evidence,
    EvidenceType,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    PullRequest,
    Requirement,
)
from app.investigation.schemas import (
    ChangeImpactAnalysis,
    CodeEvidenceBuildResult,
    CodeInvestigationResult,
    CodeObservation,
    CodeObservationType,
    FilePromotionAnalysis,
    FileReviewItem,
    FileReviewPriority,
    InvestigationPipelineInput,
    InvestigationResultBundle,
    RequirementAnalysis,
    RequirementAwareCodeInvestigationInput,
)
from app.investigation.service import (
    ChangeImpactAnalysisService,
    CodeEvidenceBuilder,
    RequirementAnalysisService,
    RequirementAwareCodeInvestigationService,
    SuspiciousFilePromotionService,
)

logger = logging.getLogger("investigation.pipeline")

_INVESTIGATION_ID_PATTERN = re.compile(r"^INV-[0-9]+$")


class EmptyChangesetError(ValueError):
    """Raised when an investigation requires a non-empty changeset but none is provided."""


# ---------------------------------------------------------------------------
# FindingSynthesisService
# ---------------------------------------------------------------------------

class FindingSynthesisService:
    """
    Deterministic synthesis component mapping criteria, code observations,
    and generated evidence into canonical domain Finding objects.

    Invariants:
    1. Every acceptance criterion from the requirement is represented by a Finding.
    2. Findings only cite evidence IDs that exist in the provided CodeEvidenceBuildResult.
    3. A criterion without supporting evidence is marked NOT_VERIFIED (never VERIFIED).
    4. Keyword presence alone does not mark a criterion VERIFIED.
    5. Observations indicating potential security concerns, bare exception handlers,
       or unresolved TODO/FIXME markers trigger REQUIRES_REVIEW status.
    6. Positive code observations without risk markers yield PARTIALLY_VERIFIED status,
       acknowledging that full verification requires dynamic runtime validation.
    7. Static source-code observations alone never produce VERIFIED status.
       Comments, docstrings, or descriptions containing 'verified' cannot prove requirement
       satisfaction. VERIFIED status is reserved for future trusted verification mechanisms
       (such as structured test execution results).
    """

    def synthesize(
        self,
        requirement: Requirement,
        observations: list[CodeObservation],
        evidence_build_result: CodeEvidenceBuildResult,
        start_id: int = 1,
    ) -> list[Finding]:
        """
        Synthesize canonical Finding objects for requirement acceptance criteria.

        Parameters
        ----------
        requirement : Requirement
            The domain requirement containing acceptance criteria to evaluate.
        observations : list[CodeObservation]
            Code observations produced during code investigation.
        evidence_build_result : CodeEvidenceBuildResult
            Result from CodeEvidenceBuilder containing evidence list and
            observation_id -> evidence_id mapping.
        start_id : int, optional
            Starting sequence number for deterministic finding IDs (default 1 -> FIND-1).

        Returns
        -------
        list[Finding]
            Deterministic list of Finding domain objects corresponding to criteria.
        """
        if start_id < 1:
            raise ValueError(f"start_id must be >= 1, got {start_id}")

        obs_to_ev = evidence_build_result.observation_to_evidence

        # Index observations by criterion_id
        obs_by_criterion: dict[str, list[CodeObservation]] = defaultdict(list)
        for obs in observations:
            # Check criterion_ids list
            for cid in obs.criterion_ids:
                obs_by_criterion[cid].append(obs)
            # Check singular criterion_id if not already added
            if obs.criterion_id and obs.criterion_id not in obs.criterion_ids:
                obs_by_criterion[obs.criterion_id].append(obs)

        findings: list[Finding] = []
        finding_counter = start_id

        # 1. Synthesize findings for each acceptance criterion
        for ac in requirement.acceptance_criteria:
            finding_id = f"FIND-{finding_counter}"
            finding_counter += 1

            matched_obs = obs_by_criterion.get(ac.criterion_id, [])

            # Resolve concrete evidence IDs for matched observations
            ev_ids: list[str] = []
            for obs in matched_obs:
                if obs.observation_id in obs_to_ev:
                    ev_ids.append(obs_to_ev[obs.observation_id])

            # Deduplicate preserving order
            unique_ev_ids = list(dict.fromkeys(ev_ids))

            if not unique_ev_ids:
                status = FindingStatus.NOT_VERIFIED
                title = f"Unverified: {ac.title}"
                explanation = (
                    f"No code observations or evidence found in the changeset for criterion "
                    f"'{ac.criterion_id}' ({ac.title}). Absence of observations does not establish satisfaction."
                )
            else:
                # Assess observation types and descriptions for potential concerns
                risk_types = {CodeObservationType.SECURITY, CodeObservationType.ERROR_HANDLING}
                has_risk = any(obs.observation_type in risk_types for obs in matched_obs)
                has_todo = any(
                    "TODO" in obs.description.upper() or "FIXME" in obs.description.upper()
                    for obs in matched_obs
                )

                if has_risk or has_todo:
                    status = FindingStatus.REQUIRES_REVIEW
                    title = f"Review required: {ac.title}"
                    concerns: list[str] = []
                    for obs in matched_obs:
                        if (
                            obs.observation_type in risk_types
                            or "TODO" in obs.description.upper()
                            or "FIXME" in obs.description.upper()
                        ):
                            concerns.append(f"{obs.description} (at {obs.file_path})")
                    explanation = (
                        f"Code observations for criterion '{ac.criterion_id}' ({ac.title}) "
                        f"indicate potential concerns or unresolved items requiring reviewer attention: "
                        f"{'; '.join(concerns)}."
                    )
                else:
                    # In Phase 1 static AST/pattern analysis, observed code proves logic presence
                    # but cannot prove complete verification (VERIFIED is reserved for trusted test execution).
                    status = FindingStatus.PARTIALLY_VERIFIED
                    title = f"Partially verified: {ac.title}"
                    explanation = (
                        f"Observed {len(unique_ev_ids)} concrete code evidence reference(s) relevant to "
                        f"criterion '{ac.criterion_id}' ({ac.title}). Implementation logic is present, "
                        f"but dynamic runtime testing or manual review is required for complete confirmation."
                    )

            finding = Finding(
                finding_id=finding_id,
                criterion_id=ac.criterion_id,
                status=status,
                title=title,
                explanation=explanation,
                evidence_ids=unique_ev_ids,
            )
            findings.append(finding)

        return findings


# ---------------------------------------------------------------------------
# InvestigationPipelineOrchestrator
# ---------------------------------------------------------------------------

class InvestigationPipelineOrchestrator:
    """
    Deterministic orchestrator executing the DevSense investigation pipeline.

    Coordinates requirement breakdown, changeset impact classification,
    suspicious file screening, code-level AST investigation, concrete evidence
    building, and finding synthesis into a validated InvestigationResultBundle.
    """

    def __init__(
        self,
        requirement_service: RequirementAnalysisService | None = None,
        impact_service: ChangeImpactAnalysisService | None = None,
        promotion_service: SuspiciousFilePromotionService | None = None,
        code_investigator: RequirementAwareCodeInvestigationService | None = None,
        evidence_builder: CodeEvidenceBuilder | None = None,
        finding_synthesizer: FindingSynthesisService | None = None,
        *,
        allow_empty_changeset: bool = True,
    ) -> None:
        self.requirement_service = requirement_service or RequirementAnalysisService()
        self.impact_service = impact_service or ChangeImpactAnalysisService()
        self.promotion_service = promotion_service or SuspiciousFilePromotionService()
        self.code_investigator = code_investigator or RequirementAwareCodeInvestigationService()
        self.evidence_builder = evidence_builder or CodeEvidenceBuilder()
        self.finding_synthesizer = finding_synthesizer or FindingSynthesisService()
        self.allow_empty_changeset = allow_empty_changeset

    def orchestrate(
        self,
        input_data: InvestigationPipelineInput,
        *,
        investigation_id: str | None = None,
    ) -> InvestigationResultBundle:
        """
        Execute the full investigation pipeline on the provided input contract.

        Parameters
        ----------
        input_data : InvestigationPipelineInput
            Validated input contract containing PullRequest, Requirement,
            and optional file contents.
        investigation_id : str | None, optional
            Optional explicit investigation ID override (must match pattern 'INV-<number>').
            If not provided, uses input_data.investigation_id or defaults to 'INV-<pr_number>'.

        Returns
        -------
        InvestigationResultBundle
            Validated result bundle containing investigation record, findings,
            concrete evidence, and intermediate stage artifacts.

        Raises
        ------
        TypeError
            If input_data is not an instance of InvestigationPipelineInput.
        EmptyChangesetError
            If changeset is empty and allow_empty_changeset is False.
        """
        if not isinstance(input_data, InvestigationPipelineInput):
            raise TypeError(
                f"input_data must be an instance of InvestigationPipelineInput, "
                f"got {type(input_data).__name__}"
            )

        # 1. Establish deterministic investigation ID
        # Note: Defaults to 'INV-<pr_number>' conforming to '^INV-[0-9]+$'. When running across
        # multiple repositories or repeated runs on the same PR where global persistence uniqueness
        # is required, callers should provide an explicit unique ID (e.g. 'INV-<unique_number>').
        inv_id = investigation_id or input_data.investigation_id or f"INV-{input_data.pull_request.number}"
        if not _INVESTIGATION_ID_PATTERN.fullmatch(inv_id):
            raise ValueError(
                f"investigation_id '{inv_id}' is invalid; must match pattern 'INV-<number>' (e.g. 'INV-1')"
            )

        # State tracking for intermediate artifacts
        req_analysis: RequirementAnalysis | None = None
        impact_analysis: ChangeImpactAnalysis | None = None
        promotion_analysis: FilePromotionAnalysis | None = None
        code_investigation_results: list[CodeInvestigationResult] = []
        ev_build_result: CodeEvidenceBuildResult | None = None
        findings: list[Finding] = []

        try:
            # 2. Stage 1: Requirement Analysis
            req_analysis = self.requirement_service.analyze(input_data.requirement)

            # 3. Handle empty changeset edge case explicitly
            if not input_data.pull_request.changed_files:
                if not self.allow_empty_changeset:
                    raise EmptyChangesetError(
                        f"Pull request #{input_data.pull_request.number} contains no changed files; "
                        "cannot perform change impact analysis."
                    )
                return self._handle_empty_changeset(
                    input_data=input_data,
                    investigation_id=inv_id,
                    requirement_analysis=req_analysis,
                )

            # 4. Stage 2: Change Impact Analysis
            impact_analysis = self.impact_service.analyze(
                req_analysis,
                input_data.pull_request.changed_files,
            )

            # 5. Stage 3: Suspicious File Promotion Screening
            promotion_analysis = self.promotion_service.promote(
                impact_analysis,
                input_data.pull_request.changed_files,
            )

            # 6. Stage 4: Code Investigation on Primary & Promoted files
            prioritized_items = [
                item for item in promotion_analysis.files
                if item.priority in (FileReviewPriority.PRIMARY, FileReviewPriority.PROMOTED)
            ]

            raw_observations: list[CodeObservation] = []
            obs_counter = 1

            for item in prioritized_items:
                file_content = input_data.file_contents.get(item.file_path, "")
                investigation_input = RequirementAwareCodeInvestigationInput(
                    requirement_analysis=req_analysis,
                    file_path=item.file_path,
                    file_content=file_content,
                    review_priority=item.priority,
                )
                file_result = self.code_investigator.investigate(investigation_input)

                # Re-index observations globally to guarantee globally unique observation IDs across files
                reindexed_observations: list[CodeObservation] = []
                for obs in file_result.observations:
                    reindexed_obs = obs.model_copy(
                        update={"observation_id": f"CODE-{obs_counter}"}
                    )
                    obs_counter += 1
                    reindexed_observations.append(reindexed_obs)
                    raw_observations.append(reindexed_obs)

                clean_file_result = CodeInvestigationResult(
                    file_path=item.file_path,
                    observations=reindexed_observations,
                )
                code_investigation_results.append(clean_file_result)

            # Determine head commit SHA if available in pull request commits.
            # Assumes commits are in chronological order (standard GitHub API ordering),
            # with commits[-1] representing the head commit. Defaults to None if commits list is empty.
            commit_sha = (
                input_data.pull_request.commits[-1].sha
                if input_data.pull_request.commits
                else None
            )

            # 7. Stage 5: Concrete Evidence Building
            ev_build_result = self.evidence_builder.build(
                raw_observations,
                repository=input_data.pull_request.repository,
                commit_sha=commit_sha,
                start_id=1,
            )

            # 8. Stage 6: Finding Synthesis
            findings = self.finding_synthesizer.synthesize(
                requirement=input_data.requirement,
                observations=raw_observations,
                evidence_build_result=ev_build_result,
                start_id=1,
            )

            # 9. Stage 7: Assemble Investigation Record & Bundle
            # Note: InvestigationStatus.COMPLETED (and bundle.is_successful) signifies pipeline
            # execution completion without runtime errors, while requirement verification outcomes
            # are tracked individually in findings[].status.
            investigation = Investigation(
                investigation_id=inv_id,
                pull_request_number=input_data.pull_request.number,
                requirement_id=input_data.requirement.requirement_id,
                status=InvestigationStatus.COMPLETED,
                finding_ids=[f.finding_id for f in findings],
                created_at=datetime.now(timezone.utc),
            )

            return InvestigationResultBundle(
                investigation=investigation,
                findings=findings,
                evidence=ev_build_result.evidence,
                requirement_analysis=req_analysis,
                impact_analysis=impact_analysis,
                promotion_analysis=promotion_analysis,
                code_investigation_results=code_investigation_results,
                evidence_build_result=ev_build_result,
                errors=[],
            )

        except EmptyChangesetError:
            raise
        except Exception as exc:
            logger.error(
                "Investigation pipeline failure for %s PR #%d: %s",
                input_data.pull_request.repository,
                input_data.pull_request.number,
                exc,
                exc_info=True,
            )
            return self._build_failed_bundle(
                input_data=input_data,
                investigation_id=inv_id,
                error_message=f"Pipeline execution failure: {type(exc).__name__}: {str(exc)}",
                partial_req_analysis=req_analysis,
                partial_impact_analysis=impact_analysis,
                partial_promotion_analysis=promotion_analysis,
                partial_code_results=code_investigation_results,
            )

    def _handle_empty_changeset(
        self,
        input_data: InvestigationPipelineInput,
        investigation_id: str,
        requirement_analysis: RequirementAnalysis,
    ) -> InvestigationResultBundle:
        """
        Produce a deterministic completed bundle for pull requests with zero changed files.
        """
        findings: list[Finding] = []
        for idx, ac in enumerate(input_data.requirement.acceptance_criteria, start=1):
            findings.append(
                Finding(
                    finding_id=f"FIND-{idx}",
                    criterion_id=ac.criterion_id,
                    status=FindingStatus.NOT_VERIFIED,
                    title=f"Unverified: {ac.title}",
                    explanation=(
                        f"Pull request #{input_data.pull_request.number} changeset is empty. "
                        f"No changed files are available to analyze or verify against criterion "
                        f"'{ac.criterion_id}' ({ac.title})."
                    ),
                    evidence_ids=[],
                )
            )

        investigation = Investigation(
            investigation_id=investigation_id,
            pull_request_number=input_data.pull_request.number,
            requirement_id=input_data.requirement.requirement_id,
            status=InvestigationStatus.COMPLETED,
            finding_ids=[f.finding_id for f in findings],
            created_at=datetime.now(timezone.utc),
        )

        return InvestigationResultBundle(
            investigation=investigation,
            findings=findings,
            evidence=[],
            requirement_analysis=requirement_analysis,
            impact_analysis=None,
            promotion_analysis=None,
            code_investigation_results=[],
            evidence_build_result=None,
            errors=[],
        )

    def _build_failed_bundle(
        self,
        input_data: InvestigationPipelineInput,
        investigation_id: str,
        error_message: str,
        partial_req_analysis: RequirementAnalysis | None = None,
        partial_impact_analysis: ChangeImpactAnalysis | None = None,
        partial_promotion_analysis: FilePromotionAnalysis | None = None,
        partial_code_results: list[CodeInvestigationResult] | None = None,
    ) -> InvestigationResultBundle:
        """
        Produce a validated failed bundle with non-empty diagnostic errors.
        """
        investigation = Investigation(
            investigation_id=investigation_id,
            pull_request_number=input_data.pull_request.number,
            requirement_id=input_data.requirement.requirement_id,
            status=InvestigationStatus.FAILED,
            finding_ids=[],
            created_at=datetime.now(timezone.utc),
        )

        return InvestigationResultBundle(
            investigation=investigation,
            findings=[],
            evidence=[],
            requirement_analysis=partial_req_analysis,
            impact_analysis=partial_impact_analysis,
            promotion_analysis=partial_promotion_analysis,
            code_investigation_results=partial_code_results or [],
            evidence_build_result=None,
            errors=[error_message],
        )
