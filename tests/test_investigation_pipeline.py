"""
DevSense — Investigation Pipeline Orchestrator Tests
=====================================================
Unit tests for InvestigationPipelineOrchestrator, FindingSynthesisService,
and EmptyChangesetError.
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from app.domain.models import (
    AcceptanceCriterion,
    ChangedFile,
    Commit,
    Evidence,
    EvidenceType,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    PullRequest,
    Requirement,
)
from app.investigation.pipeline import (
    EmptyChangesetError,
    FindingSynthesisService,
    InvestigationPipelineOrchestrator,
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
    InvestigationPipelineInput,
    InvestigationResultBundle,
    RequirementAnalysis,
    RequirementAwareCodeInvestigationInput,
)
from app.investigation.service import (
    ChangeImpactAnalysisService,
    CodeEvidenceBuilder,
    CodeInvestigationService,
    RequirementAnalysisService,
    RequirementAwareCodeInvestigationService,
    SuspiciousFilePromotionService,
)


# =========================================================================
# Shared Fixture Helpers
# =========================================================================

class BasePipelineTestCase(unittest.TestCase):
    """Shared helpers for constructing valid domain fixtures."""

    def _make_criterion(
        self,
        cid: str = "AC-1",
        title: str = "User authentication verification",
        description: str = "Authenticate user credentials with password check",
    ) -> AcceptanceCriterion:
        return AcceptanceCriterion(
            criterion_id=cid,
            title=title,
            description=description,
            is_met=False,
        )

    def _make_requirement(
        self,
        req_id: str = "REQ-101",
        criteria: list[AcceptanceCriterion] | None = None,
    ) -> Requirement:
        if criteria is None:
            criteria = [
                self._make_criterion("AC-1", "Validate user credentials", "Check user login credentials"),
                self._make_criterion("AC-2", "Emit audit log", "Log every authentication event"),
            ]
        return Requirement(
            requirement_id=req_id,
            title="User Authentication",
            description="Secure authentication flow with credential validation and audit logging",
            status="Active",
            acceptance_criteria=criteria,
        )

    def _make_pull_request(
        self,
        pr_number: int = 42,
        changed_files: list[ChangedFile] | None = None,
    ) -> PullRequest:
        if changed_files is None:
            changed_files = [
                ChangedFile(path="app/auth/login.py", status="modified", additions=60, deletions=5),
                ChangedFile(path="requirements.txt", status="modified", additions=2, deletions=1),
                ChangedFile(path="docs/notes.txt", status="modified", additions=5, deletions=0),
            ]
        return PullRequest(
            repository="SurajD45/devsense",
            number=pr_number,
            title="Implement user login and audit logging",
            description="Adds login verification and audit events",
            author="suraj",
            source_branch="feature/auth",
            target_branch="main",
            state="open",
            commits=[
                Commit(
                    sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
                    message="feat: login and audit",
                    author="suraj",
                )
            ],
            changed_files=changed_files,
        )

    def _make_pipeline_input(
        self,
        pr: PullRequest | None = None,
        req: Requirement | None = None,
        file_contents: dict[str, str] | None = None,
        investigation_id: str | None = None,
    ) -> InvestigationPipelineInput:
        if pr is None:
            pr = self._make_pull_request()
        if req is None:
            req = self._make_requirement()
        if file_contents is None:
            file_contents = {
                "app/auth/login.py": (
                    "def authenticate_user(username, password):\n"
                    "    # validate user credentials\n"
                    "    if not username:\n"
                    "        raise ValueError('Invalid user')\n"
                    "    return True\n"
                ),
                "requirements.txt": "cryptography==42.0.0\n",
                "docs/notes.txt": "Implementation notes.\n",
            }
        return InvestigationPipelineInput(
            pull_request=pr,
            requirement=req,
            file_contents=file_contents,
            investigation_id=investigation_id,
        )


# =========================================================================
# FindingSynthesisService Tests
# =========================================================================

class TestFindingSynthesisService(BasePipelineTestCase):
    """Unit tests for deterministic finding synthesis."""

    def setUp(self):
        self.synthesizer = FindingSynthesisService()

    def test_start_id_validation_raises(self):
        req = self._make_requirement()
        ev_build = CodeEvidenceBuildResult(evidence=[], observation_to_evidence={})
        with self.assertRaises(ValueError):
            self.synthesizer.synthesize(req, [], ev_build, start_id=0)

    def test_criterion_with_no_observations_marked_not_verified(self):
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        ev_build = CodeEvidenceBuildResult(evidence=[], observation_to_evidence={})
        findings = self.synthesizer.synthesize(req, [], ev_build)

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.finding_id, "FIND-1")
        self.assertEqual(finding.criterion_id, "AC-1")
        self.assertEqual(finding.status, FindingStatus.NOT_VERIFIED)
        self.assertEqual(finding.evidence_ids, [])
        self.assertIn("No code observations", finding.explanation)
        self.assertIn("Absence of observations does not establish satisfaction", finding.explanation)

    def test_criterion_with_positive_code_observation_marked_partially_verified(self):
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=CodeObservationType.LOGIC,
            symbol="authenticate_user",
            description="Function authenticate_user handles user credentials",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="Function authenticate_user",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs], ev_build)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.finding_id, "FIND-1")
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1"])
        self.assertIn("Partially verified", finding.title)

    def test_criterion_with_risk_marker_marked_requires_review(self):
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=CodeObservationType.SECURITY,
            symbol="authenticate_user",
            description="Potential hardcoded credential or secret detected",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="Security risk observation",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs], ev_build)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertEqual(finding.evidence_ids, ["EV-1"])
        self.assertIn("Review required", finding.title)

    def test_criterion_with_todo_comment_marked_requires_review(self):
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=3,
            end_line=3,
            observation_type=CodeObservationType.LOGIC,
            symbol="authenticate_user",
            description="TODO: complete password hashing check",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=3,
            end_line=3,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="TODO comment",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs], ev_build)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertEqual(finding.evidence_ids, ["EV-1"])

    def test_observation_description_starting_with_verified_does_not_produce_verified(self):
        """Static observation description starting with 'Verified' must NOT yield VERIFIED status."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=CodeObservationType.LOGIC,
            symbol="authenticate_user",
            description="Verified: user authentication is fully implemented and tested",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="Explicit verification comment",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs], ev_build)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        # In Phase 1 static analysis, must be PARTIALLY_VERIFIED, never VERIFIED
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1"])
        self.assertIn("Partially verified", finding.title)

    def test_observation_description_containing_verified_does_not_produce_verified(self):
        """Static observation description containing 'verified:' must NOT yield VERIFIED status."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=CodeObservationType.LOGIC,
            symbol="authenticate_user",
            description="Implementation logic verified: password hashing check",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="Implementation logic",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs], ev_build)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1"])

    def test_multiple_criteria_synthesis_ordering_and_referential_integrity(self):
        ac1 = self._make_criterion("AC-1", "Credential check")
        ac2 = self._make_criterion("AC-2", "Audit logging")
        req = self._make_requirement(criteria=[ac1, ac2])

        obs1 = CodeObservation(
            observation_id="CODE-1",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=CodeObservationType.LOGIC,
            symbol="authenticate_user",
            description="Authentication logic",
            criterion_id="AC-1",
            criterion_ids=["AC-1"],
        )
        evidence1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="SurajD45/devsense",
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description="Authentication logic",
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence1],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        findings = self.synthesizer.synthesize(req, [obs1], ev_build, start_id=10)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].finding_id, "FIND-10")
        self.assertEqual(findings[0].criterion_id, "AC-1")
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(findings[0].evidence_ids, ["EV-1"])

        self.assertEqual(findings[1].finding_id, "FIND-11")
        self.assertEqual(findings[1].criterion_id, "AC-2")
        self.assertEqual(findings[1].status, FindingStatus.NOT_VERIFIED)
        self.assertEqual(findings[1].evidence_ids, [])

        # Ensure all referenced evidence IDs exist in ev_build.evidence
        all_ev_ids = {e.evidence_id for e in ev_build.evidence}
        for f in findings:
            for eid in f.evidence_ids:
                self.assertIn(eid, all_ev_ids)


# =========================================================================
# InvestigationPipelineOrchestrator Tests
# =========================================================================

class TestInvestigationPipelineOrchestrator(BasePipelineTestCase):
    """Unit tests for InvestigationPipelineOrchestrator."""

    def setUp(self):
        self.orchestrator = InvestigationPipelineOrchestrator()

    def test_type_error_for_invalid_input(self):
        with self.assertRaises(TypeError):
            self.orchestrator.orchestrate(None)  # type: ignore

        with self.assertRaises(TypeError):
            self.orchestrator.orchestrate({})  # type: ignore

    def test_invalid_investigation_id_pattern_raises(self):
        pipeline_input = self._make_pipeline_input()
        with self.assertRaises(ValueError) as ctx:
            self.orchestrator.orchestrate(pipeline_input, investigation_id="INVALID-99")
        self.assertIn("must match pattern 'INV-<number>'", str(ctx.exception))

    def test_successful_full_pipeline_run(self):
        pipeline_input = self._make_pipeline_input()
        bundle = self.orchestrator.orchestrate(pipeline_input)

        # 1. Investigation record assertions
        self.assertIsInstance(bundle, InvestigationResultBundle)
        self.assertEqual(bundle.investigation.investigation_id, "INV-42")
        self.assertEqual(bundle.investigation.pull_request_number, 42)
        self.assertEqual(bundle.investigation.requirement_id, "REQ-101")
        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertEqual(bundle.errors, [])

        # 2. Stage 1: Requirement Analysis
        self.assertIsNotNone(bundle.requirement_analysis)
        self.assertEqual(bundle.requirement_analysis.requirement_id, "REQ-101")
        self.assertEqual(len(bundle.requirement_analysis.criteria), 2)

        # 3. Stage 2: Change Impact Analysis
        self.assertIsNotNone(bundle.impact_analysis)
        self.assertGreaterEqual(len(bundle.impact_analysis.classifications), 3)

        # 4. Stage 3: Suspicious File Promotion Analysis
        self.assertIsNotNone(bundle.promotion_analysis)
        promoted_files = [
            item.file_path for item in bundle.promotion_analysis.files
            if item.priority == FileReviewPriority.PROMOTED
        ]
        # requirements.txt matches dependency signal
        self.assertIn("requirements.txt", promoted_files)

        primary_files = [
            item.file_path for item in bundle.promotion_analysis.files
            if item.priority == FileReviewPriority.PRIMARY
        ]
        self.assertIn("app/auth/login.py", primary_files)

        # docs/notes.txt remains SECONDARY (not promoted)
        secondary_files = [
            item.file_path for item in bundle.promotion_analysis.files
            if item.priority == FileReviewPriority.SECONDARY
        ]
        self.assertIn("docs/notes.txt", secondary_files)

        # 5. Stage 4: Code Investigation Results
        # Deep inspection should only have run on PRIMARY and PROMOTED files, NOT ordinary SECONDARY
        investigated_paths = [res.file_path for res in bundle.code_investigation_results]
        self.assertIn("app/auth/login.py", investigated_paths)
        self.assertIn("requirements.txt", investigated_paths)
        self.assertNotIn("docs/notes.txt", investigated_paths)

        # 6. Stage 5: Evidence Build Result
        self.assertIsNotNone(bundle.evidence_build_result)
        self.assertIsInstance(bundle.evidence, list)

        # 7. Stage 6: Findings & Referential Integrity
        self.assertEqual(len(bundle.findings), 2)
        bundle_evidence_ids = {e.evidence_id for e in bundle.evidence}
        for finding in bundle.findings:
            self.assertTrue(finding.finding_id.startswith("FIND-"))
            # Invariant: Every evidence ID cited in a finding must exist in bundle.evidence
            for ev_id in finding.evidence_ids:
                self.assertIn(ev_id, bundle_evidence_ids)

        # Invariant: investigation.finding_ids matches bundle.findings
        self.assertEqual(bundle.investigation.finding_ids, [f.finding_id for f in bundle.findings])

    def test_observation_reindexing_prevents_duplicate_id_collision(self):
        """
        When multiple files produce observations, each file's investigate() call
        resets its counter to 'CODE-1'. The orchestrator must globally re-index
        observations so CodeEvidenceBuilder does not fail with duplicate ID error.
        """
        pr = self._make_pull_request(
            changed_files=[
                ChangedFile(path="app/auth/login.py", status="modified", additions=20, deletions=2),
                ChangedFile(path="app/auth/session.py", status="modified", additions=25, deletions=3),
            ]
        )
        file_contents = {
            "app/auth/login.py": "def login(): pass\n",
            "app/auth/session.py": "def session(): pass\n",
        }
        pipeline_input = self._make_pipeline_input(pr=pr, file_contents=file_contents)

        # Run orchestrator
        bundle = self.orchestrator.orchestrate(pipeline_input)
        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)

        # Check all observations have unique IDs
        all_obs_ids = [
            obs.observation_id
            for res in bundle.code_investigation_results
            for obs in res.observations
        ]
        self.assertEqual(len(all_obs_ids), len(set(all_obs_ids)))

    def test_criterion_without_evidence_is_not_verified(self):
        """A criterion with no matching code observations must never be marked VERIFIED."""
        # AC-99 does not match any code in login.py
        req = self._make_requirement(
            criteria=[self._make_criterion("AC-99", "Blockchain ledger syncing", "Sync transactions with blockchain")]
        )
        pipeline_input = self._make_pipeline_input(req=req)
        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertEqual(len(bundle.findings), 1)
        finding = bundle.findings[0]
        self.assertEqual(finding.criterion_id, "AC-99")
        self.assertEqual(finding.status, FindingStatus.NOT_VERIFIED)
        self.assertEqual(finding.evidence_ids, [])
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)

    def test_empty_changeset_default_behavior(self):
        """Default orchestrator accepts empty changeset and returns completed bundle with NOT_VERIFIED findings."""
        pr = self._make_pull_request(changed_files=[])
        pipeline_input = self._make_pipeline_input(pr=pr, file_contents={})

        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertIsInstance(bundle, InvestigationResultBundle)
        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertEqual(bundle.investigation.investigation_id, "INV-42")
        self.assertEqual(bundle.evidence, [])
        self.assertIsNone(bundle.impact_analysis)
        self.assertIsNone(bundle.promotion_analysis)
        self.assertEqual(bundle.code_investigation_results, [])
        self.assertIsNone(bundle.evidence_build_result)
        self.assertEqual(bundle.errors, [])

        self.assertEqual(len(bundle.findings), 2)
        for finding in bundle.findings:
            self.assertEqual(finding.status, FindingStatus.NOT_VERIFIED)
            self.assertEqual(finding.evidence_ids, [])
            self.assertIn("changeset is empty", finding.explanation)

    def test_empty_changeset_raises_when_disallowed(self):
        """When allow_empty_changeset=False, empty changeset raises EmptyChangesetError."""
        strict_orchestrator = InvestigationPipelineOrchestrator(allow_empty_changeset=False)
        pr = self._make_pull_request(changed_files=[])
        pipeline_input = self._make_pipeline_input(pr=pr, file_contents={})

        with self.assertRaises(EmptyChangesetError) as ctx:
            strict_orchestrator.orchestrate(pipeline_input)
        self.assertIn("contains no changed files", str(ctx.exception))

    def test_service_failure_produces_failed_bundle_with_diagnostics(self):
        """Underlying service exception returns FAILED status and non-empty diagnostic errors."""
        mock_impact_service = MagicMock(spec=ChangeImpactAnalysisService)
        mock_impact_service.analyze.side_effect = RuntimeError("AST engine failure during impact analysis")

        orchestrator = InvestigationPipelineOrchestrator(impact_service=mock_impact_service)
        pipeline_input = self._make_pipeline_input()

        bundle = orchestrator.orchestrate(pipeline_input)

        self.assertIsInstance(bundle, InvestigationResultBundle)
        self.assertEqual(bundle.investigation.status, InvestigationStatus.FAILED)
        self.assertNotEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertEqual(bundle.findings, [])
        self.assertEqual(bundle.evidence, [])
        self.assertGreaterEqual(len(bundle.errors), 1)
        self.assertIn("Pipeline execution failure", bundle.errors[0])
        self.assertIn("AST engine failure", bundle.errors[0])

    def test_custom_and_deterministic_investigation_id(self):
        """Explicit investigation_id overrides default INV-<pr_number>."""
        pipeline_input = self._make_pipeline_input()
        bundle = self.orchestrator.orchestrate(pipeline_input, investigation_id="INV-999")
        self.assertEqual(bundle.investigation.investigation_id, "INV-999")

    def test_investigation_id_from_pipeline_input(self):
        """Investigation ID provided in input contract is respected."""
        pipeline_input = self._make_pipeline_input(investigation_id="INV-888")
        bundle = self.orchestrator.orchestrate(pipeline_input)
        self.assertEqual(bundle.investigation.investigation_id, "INV-888")

    def test_custom_service_dependency_injection(self):
        """Constructor allows injecting custom mock or specialized services."""
        mock_req_service = MagicMock(spec=RequirementAnalysisService)
        mock_req_service.analyze.return_value = RequirementAnalysis(
            requirement_id="REQ-CUSTOM",
            summary="Custom requirement analysis",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Custom interpretation",
                    verification_points=["Verify custom point"],
                    potential_areas=["custom_area"],
                )
            ],
        )

        orchestrator = InvestigationPipelineOrchestrator(requirement_service=mock_req_service)
        pipeline_input = self._make_pipeline_input()
        bundle = orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.requirement_analysis.requirement_id, "REQ-CUSTOM")
        mock_req_service.analyze.assert_called_once()

    def test_pipeline_failure_preserves_partial_intermediate_artifacts(self):
        """When failure occurs late in pipeline, completed earlier artifacts are retained in failed bundle."""
        mock_ev_builder = MagicMock(spec=CodeEvidenceBuilder)
        mock_ev_builder.build.side_effect = RuntimeError("Evidence store network failure")

        orchestrator = InvestigationPipelineOrchestrator(evidence_builder=mock_ev_builder)
        pipeline_input = self._make_pipeline_input()

        bundle = orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.investigation.status, InvestigationStatus.FAILED)
        self.assertIsNotNone(bundle.requirement_analysis)
        self.assertIsNotNone(bundle.impact_analysis)
        self.assertIsNotNone(bundle.promotion_analysis)
        self.assertGreaterEqual(len(bundle.code_investigation_results), 1)
        self.assertIsNone(bundle.evidence_build_result)
        self.assertEqual(bundle.findings, [])
        self.assertIn("Evidence store network failure", bundle.errors[0])

    def test_input_validation_boundary_rejects_missing_acceptance_criteria(self):
        """Input boundary strictly prevents creating pipeline input without acceptance criteria."""
        pr = self._make_pull_request()
        from pydantic import ValidationError

        req_without_ac = Requirement(
            requirement_id="REQ-EMPTY",
            title="Invalid Empty Requirement",
            description="No acceptance criteria",
            status="Active",
            acceptance_criteria=[],
        )

        with self.assertRaises(ValidationError) as ctx:
            InvestigationPipelineInput(
                pull_request=pr,
                requirement=req_without_ac,
            )
        self.assertIn("must contain at least one AcceptanceCriterion", str(ctx.exception))

    def test_missing_file_contents_defaults_gracefully(self):
        """Files without explicit file_contents default to empty string without crashing."""
        pr = self._make_pull_request(
            changed_files=[
                ChangedFile(path="app/auth/login.py", status="modified", additions=10, deletions=1)
            ]
        )
        pipeline_input = self._make_pipeline_input(pr=pr, file_contents={})
        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertEqual(len(bundle.code_investigation_results), 1)
        self.assertEqual(bundle.code_investigation_results[0].file_path, "app/auth/login.py")

    def test_source_comment_claiming_verified_cannot_establish_verification(self):
        """A source file comment claiming '# verified: completed' cannot produce VERIFIED in static pipeline."""
        pr = self._make_pull_request(
            changed_files=[
                ChangedFile(path="app/auth/login.py", status="modified", additions=15, deletions=1)
            ]
        )
        source_with_verified_comment = (
            "def authenticate_user(username, password):\n"
            "    # verified: authentication completed and tested\n"
            "    # validate user credentials\n"
            "    return True\n"
        )
        pipeline_input = self._make_pipeline_input(
            pr=pr,
            file_contents={"app/auth/login.py": source_with_verified_comment},
        )
        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertEqual(len(bundle.findings), 2)
        # Source comment alone does not constitute verification evidence, so status must be NOT_VERIFIED, never VERIFIED
        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertEqual(ac1_finding.status, FindingStatus.NOT_VERIFIED)

        # Referential integrity: every finding evidence ID must be in bundle.evidence
        bundle_evidence_ids = {e.evidence_id for e in bundle.evidence}
        for finding in bundle.findings:
            for eid in finding.evidence_ids:
                self.assertIn(eid, bundle_evidence_ids)

    def test_source_todo_comment_mentioning_verified_yields_requires_review(self):
        """A source TODO comment claiming '# TODO: verified: completed' yields REQUIRES_REVIEW, never VERIFIED."""
        pr = self._make_pull_request(
            changed_files=[
                ChangedFile(path="app/auth/login.py", status="modified", additions=15, deletions=1)
            ]
        )
        source_with_todo_verified = (
            "def authenticate_user(username, password):\n"
            "    # TODO: verified: credential validation is complete\n"
            "    return True\n"
        )
        pipeline_input = self._make_pipeline_input(
            pr=pr,
            file_contents={"app/auth/login.py": source_with_todo_verified},
        )
        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertEqual(ac1_finding.status, FindingStatus.REQUIRES_REVIEW)

    def test_risk_markers_precedence_over_static_observations(self):
        """Security and error handling risks take precedence over positive logic and yield REQUIRES_REVIEW."""
        pr = self._make_pull_request(
            changed_files=[
                ChangedFile(path="app/auth/login.py", status="modified", additions=20, deletions=2)
            ]
        )
        source_with_risk = (
            "def authenticate_user(username, password):\n"
            "    # validate user credentials\n"
            "    # TODO: replace hardcoded secret check\n"
            "    token = 'secret_token_12345'\n"
            "    try:\n"
            "        return True\n"
            "    except:\n"
            "        pass\n"
        )
        pipeline_input = self._make_pipeline_input(
            pr=pr,
            file_contents={"app/auth/login.py": source_with_risk},
        )
        bundle = self.orchestrator.orchestrate(pipeline_input)

        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertEqual(ac1_finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertNotEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
