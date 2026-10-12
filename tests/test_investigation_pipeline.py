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
    TestEvidenceBuildResult,
    TestExecutionRecord,
    TestExecutionStatus,
)
from app.investigation.service import (
    ChangeImpactAnalysisService,
    CodeEvidenceBuilder,
    CodeInvestigationService,
    RequirementAnalysisService,
    RequirementAwareCodeInvestigationService,
    SuspiciousFilePromotionService,
    TestEvidenceBuilder,
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

    def _make_test_record(
        self,
        test_id: str = "TEST-1",
        file_path: str = "tests/test_login.py",
        test_name: str = "test_authenticate_user_valid",
        status: TestExecutionStatus = TestExecutionStatus.PASSED,
        criterion_ids: list[str] | None = None,
        commit_sha: str | None = "279ee818dad7f588a305372781ee3a4a347d3c9a",
        failure_message: str | None = None,
    ) -> TestExecutionRecord:
        return TestExecutionRecord(
            test_id=test_id,
            file_path=file_path,
            test_name=test_name,
            status=status,
            criterion_ids=criterion_ids if criterion_ids is not None else ["AC-1"],
            commit_sha=commit_sha,
            failure_message=failure_message,
        )

    def _make_code_observation(
        self,
        observation_id: str = "CODE-1",
        file_path: str = "app/auth/login.py",
        description: str = "User authentication credential validation logic",
        symbol: str | None = "authenticate_user",
        start_line: int = 1,
        end_line: int = 5,
        observation_type: CodeObservationType = CodeObservationType.LOGIC,
        criterion_ids: list[str] | None = None,
    ) -> CodeObservation:
        return CodeObservation(
            observation_id=observation_id,
            file_path=file_path,
            description=description,
            symbol=symbol,
            start_line=start_line,
            end_line=end_line,
            observation_type=observation_type,
            criterion_ids=criterion_ids if criterion_ids is not None else ["AC-1"],
        )

    def _make_mock_code_investigator(
        self,
        observations_by_file: dict[str, list[CodeObservation]] | None = None,
    ) -> MagicMock:
        mock_inv = MagicMock(spec=RequirementAwareCodeInvestigationService)
        if observations_by_file is None:
            default_obs = [self._make_code_observation()]
            observations_by_file = {"app/auth/login.py": default_obs}

        def _investigate(inp: RequirementAwareCodeInvestigationInput) -> CodeInvestigationResult:
            obs = observations_by_file.get(inp.file_path, [])
            return CodeInvestigationResult(file_path=inp.file_path, observations=obs)

        mock_inv.investigate.side_effect = _investigate
        return mock_inv

    def _make_pipeline_input(
        self,
        pr: PullRequest | None = None,
        req: Requirement | None = None,
        file_contents: dict[str, str] | None = None,
        investigation_id: str | None = None,
        test_results: list[TestExecutionRecord] | None = None,
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
            test_results=test_results or [],
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
# FindingSynthesisService Test Evidence Integration Tests (Phase 2 Step 3)
# =========================================================================

class TestFindingSynthesisServiceTestEvidence(BasePipelineTestCase):
    """Unit tests for FindingSynthesisService test evidence integration."""

    def setUp(self):
        self.synthesizer = FindingSynthesisService()
        self.head_sha = "279ee818dad7f588a305372781ee3a4a347d3c9a"
        self.repository = "SurajD45/devsense"

    def _make_code_obs_and_evidence(
        self,
        cid: str = "AC-1",
        obs_id: str = "CODE-1",
        ev_id: str = "EV-1",
        obs_type: CodeObservationType = CodeObservationType.LOGIC,
        desc: str = "Function authenticate_user handles credentials",
    ) -> tuple[CodeObservation, CodeEvidenceBuildResult]:
        obs = CodeObservation(
            observation_id=obs_id,
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            observation_type=obs_type,
            symbol="authenticate_user",
            description=desc,
            criterion_id=cid,
            criterion_ids=[cid],
        )
        evidence = Evidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.CODE,
            repository=self.repository,
            file_path="app/auth/login.py",
            start_line=1,
            end_line=5,
            symbol="authenticate_user",
            commit_sha=self.head_sha,
            description=desc,
        )
        ev_build = CodeEvidenceBuildResult(
            evidence=[evidence],
            observation_to_evidence={obs_id: ev_id},
        )
        return obs, ev_build

    def _make_test_record_and_build_result(
        self,
        test_id: str = "test_auth_success",
        cid: str = "AC-1",
        status: TestExecutionStatus = TestExecutionStatus.PASSED,
        commit_sha: str | None = "279ee818dad7f588a305372781ee3a4a347d3c9a",
        failure_message: str | None = None,
        ev_id: str = "EV-2",
        criterion_ids: list[str] | None = None,
    ) -> tuple[TestExecutionRecord, TestEvidenceBuildResult]:
        if criterion_ids is None:
            criterion_ids = [cid]
        record = TestExecutionRecord(
            test_id=test_id,
            file_path="tests/test_auth.py",
            test_name=test_id,
            status=status,
            criterion_ids=criterion_ids,
            duration_ms=45.0,
            failure_message=failure_message,
            commit_sha=commit_sha,
        )
        evidence = Evidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.TEST,
            repository=self.repository,
            file_path="tests/test_auth.py",
            symbol=test_id,
            commit_sha=commit_sha,
            description=f"Test {test_id} ({status.value})",
        )
        test_ev_result = TestEvidenceBuildResult(
            evidence=[evidence],
            test_to_evidence={test_id: ev_id},
            test_records=[record],
        )
        return record, test_ev_result

    def test_linked_passed_test_with_code_evidence_and_matching_sha_produces_verified(self):
        """Linked passed test + relevant code evidence + matching PR head SHA -> VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.finding_id, "FIND-1")
        self.assertEqual(finding.criterion_id, "AC-1")
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("Verified:", finding.title)
        self.assertIn("verified by 1 passing test", finding.explanation)

    def test_linked_failed_test_produces_requires_review(self):
        """Linked failed test -> REQUIRES_REVIEW."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login",
            "AC-1",
            status=TestExecutionStatus.FAILED,
            commit_sha=self.head_sha,
            failure_message="AssertionError: expected True but got False",
            ev_id="EV-2",
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("Review required:", finding.title)
        self.assertIn("AssertionError", finding.explanation)

    def test_linked_skipped_test_never_verified(self):
        """Linked skipped test -> never VERIFIED (falls back to PARTIALLY_VERIFIED when code exists)."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.SKIPPED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("Partially verified:", finding.title)
        self.assertIn("SKIPPED", finding.explanation)

    def test_linked_not_run_test_never_verified(self):
        """Linked NOT_RUN test -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.NOT_RUN, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("NOT_RUN", finding.explanation)

    def test_passed_test_without_criterion_linkage_does_not_verify_criterion(self):
        """Passed test tagged with AC-2 does NOT verify AC-1."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        # Test is explicitly linked only to AC-2, not AC-1
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_audit", "AC-2", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2",
            criterion_ids=["AC-2"]
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        # Test evidence EV-2 must NOT be attached to AC-1 finding
        self.assertEqual(finding.evidence_ids, ["EV-1"])

    def test_passed_test_with_stale_commit_sha_never_verified(self):
        """Passed test with stale commit SHA -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        stale_sha = "1111111111111111111111111111111111111111"
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=stale_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("cannot be verified against the PR head commit", finding.explanation)

    def test_missing_commit_provenance_never_verified(self):
        """Missing commit provenance on test record or pipeline -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        # Subcase A: Test record has commit_sha=None
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=None, ev_id="EV-2"
        )
        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )
        self.assertNotEqual(findings[0].status, FindingStatus.VERIFIED)
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("missing commit_sha on test record", findings[0].explanation)

        # Subcase B: Pipeline head_commit_sha=None
        rec2, test_ev_build2 = self._make_test_record_and_build_result(
            "test_login2", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )
        findings2 = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build2,
            head_commit_sha=None,
        )
        self.assertNotEqual(findings2[0].status, FindingStatus.VERIFIED)
        self.assertEqual(findings2[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("PR head commit SHA is not provided", findings2[0].explanation)

    def test_missing_code_evidence_never_verified(self):
        """Passing test with matching SHA but zero code evidence -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        empty_code_ev_build = CodeEvidenceBuildResult(evidence=[], observation_to_evidence={})
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[],
            evidence_build_result=empty_code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-2"])
        self.assertIn("no supporting code implementation evidence", finding.explanation)

    def test_existing_risk_marker_with_passed_test_produces_requires_review(self):
        """Existing risk marker (SECURITY or TODO) + passed test -> REQUIRES_REVIEW."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence(
            "AC-1",
            "CODE-1",
            "EV-1",
            obs_type=CodeObservationType.SECURITY,
            desc="Potential hardcoded credential or secret detected",
        )
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertNotEqual(finding.status, FindingStatus.VERIFIED)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2"])
        self.assertIn("Review required:", finding.title)

    def test_multiple_linked_tests_with_at_least_one_failure_produces_requires_review(self):
        """Multiple linked tests where one passed and one failed -> REQUIRES_REVIEW."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        rec_pass = TestExecutionRecord(
            test_id="test_login_unit",
            file_path="tests/test_auth.py",
            test_name="test_login_unit",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha=self.head_sha,
        )
        rec_fail = TestExecutionRecord(
            test_id="test_login_integration",
            file_path="tests/test_auth.py",
            test_name="test_login_integration",
            status=TestExecutionStatus.FAILED,
            failure_message="ConnectionTimeout to auth service",
            criterion_ids=["AC-1"],
            commit_sha=self.head_sha,
        )
        ev_pass = Evidence(
            evidence_id="EV-2",
            evidence_type=EvidenceType.TEST,
            repository=self.repository,
            file_path="tests/test_auth.py",
            symbol="test_login_unit",
            commit_sha=self.head_sha,
            description="Unit test PASSED",
        )
        ev_fail = Evidence(
            evidence_id="EV-3",
            evidence_type=EvidenceType.TEST,
            repository=self.repository,
            file_path="tests/test_auth.py",
            symbol="test_login_integration",
            commit_sha=self.head_sha,
            description="Integration test FAILED",
        )
        test_ev_build = TestEvidenceBuildResult(
            evidence=[ev_pass, ev_fail],
            test_to_evidence={"test_login_unit": "EV-2", "test_login_integration": "EV-3"},
            test_records=[rec_pass, rec_fail],
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertEqual(finding.evidence_ids, ["EV-1", "EV-2", "EV-3"])
        self.assertIn("Review required:", finding.title)
        self.assertIn("ConnectionTimeout", finding.explanation)

    def test_no_test_evidence_supplied_preserves_phase1_behavior(self):
        """No test evidence supplied -> preserves Phase 1 synthesis behavior exactly."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        # Explicit test_evidence_build_result=None
        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=None,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(findings[0].evidence_ids, ["EV-1"])

        # Default argument call (3 args)
        findings_default = self.synthesizer.synthesize(req, [obs], code_ev_build)
        self.assertEqual(findings_default[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(findings_default[0].evidence_ids, ["EV-1"])

    def test_all_finding_evidence_ids_present_in_final_bundle(self):
        """All finding evidence IDs (code and test) remain present in the bundle evidence list."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        # Assemble bundle with combined evidence
        combined_evidence = code_ev_build.evidence + test_ev_build.evidence
        investigation = Investigation(
            investigation_id="INV-42",
            pull_request_number=42,
            requirement_id=req.requirement_id,
            status=InvestigationStatus.COMPLETED,
            finding_ids=[f.finding_id for f in findings],
            created_at=datetime.now(timezone.utc),
        )
        bundle = InvestigationResultBundle(
            investigation=investigation,
            findings=findings,
            evidence=combined_evidence,
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            errors=[],
        )

        self.assertEqual(bundle.is_successful, True)
        bundle_ev_ids = {e.evidence_id for e in bundle.evidence}
        for finding in bundle.findings:
            for ev_id in finding.evidence_ids:
                self.assertIn(ev_id, bundle_ev_ids)

    def test_commit_sha_case_normalization_produces_verified(self):
        """Uppercase commit SHA on test record matches lowercase head SHA -> VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        uppercase_sha = self.head_sha.upper()
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=uppercase_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha.lower(),
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, FindingStatus.VERIFIED)

    def test_short_commit_sha_cannot_independently_establish_provenance_never_verified(self):
        """Short 7-character commit SHA cannot establish full commit identity -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        short_sha = self.head_sha[:7]
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=short_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha=self.head_sha,
        )

        self.assertEqual(len(findings), 1)
        self.assertNotEqual(findings[0].status, FindingStatus.VERIFIED)
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("cannot be verified against the PR head commit", findings[0].explanation)

    def test_empty_or_whitespace_commit_sha_never_verified(self):
        """Whitespace head_commit_sha cannot match -> never VERIFIED."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            requirement=req,
            observations=[obs],
            evidence_build_result=code_ev_build,
            test_evidence_build_result=test_ev_build,
            head_commit_sha="   ",
        )

        self.assertEqual(len(findings), 1)
        self.assertNotEqual(findings[0].status, FindingStatus.VERIFIED)
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)

    def test_backward_compatibility_legacy_positional_start_id(self):
        """Calling synthesize with 4 positional arguments passes start_id correctly."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        # 4th argument is positional start_id=10
        findings = self.synthesizer.synthesize(req, [obs], code_ev_build, 10)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "FIND-10")
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertEqual(findings[0].evidence_ids, ["EV-1"])

    def test_backward_compatibility_legacy_positional_invalid_start_id_raises(self):
        """Calling synthesize with 4th positional argument start_id < 1 raises ValueError."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        with self.assertRaises(ValueError):
            self.synthesizer.synthesize(req, [obs], code_ev_build, 0)

    def test_backward_compatibility_legacy_keyword_start_id(self):
        """Calling synthesize with keyword start_id=15 assigns FIND-15 without test evidence."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        findings = self.synthesizer.synthesize(req, [obs], code_ev_build, start_id=15)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "FIND-15")
        self.assertEqual(findings[0].status, FindingStatus.PARTIALLY_VERIFIED)

    def test_backward_compatibility_positional_test_evidence_and_start_id(self):
        """Calling synthesize with positional test_evidence_build_result and positional start_id."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")
        rec, test_ev_build = self._make_test_record_and_build_result(
            "test_login", "AC-1", status=TestExecutionStatus.PASSED, commit_sha=self.head_sha, ev_id="EV-2"
        )

        findings = self.synthesizer.synthesize(
            req, [obs], code_ev_build, test_ev_build, 20, head_commit_sha=self.head_sha
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "FIND-20")
        self.assertEqual(findings[0].status, FindingStatus.VERIFIED)

    def test_type_error_for_invalid_test_evidence_build_result_type(self):
        """Calling synthesize with invalid test_evidence_build_result type raises TypeError."""
        req = self._make_requirement(criteria=[self._make_criterion("AC-1")])
        obs, code_ev_build = self._make_code_obs_and_evidence("AC-1", "CODE-1", "EV-1")

        with self.assertRaises(TypeError):
            self.synthesizer.synthesize(req, [obs], code_ev_build, "not_a_build_result")  # type: ignore


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

    def test_orchestration_with_test_results_includes_both_code_and_test_evidence(self):
        """Orchestration with test execution records includes both CODE and TEST evidence in the bundle."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        self.assertEqual(bundle.investigation.status, InvestigationStatus.COMPLETED)
        self.assertIsNotNone(bundle.test_evidence_build_result)
        self.assertEqual(len(bundle.test_evidence_build_result.evidence), 1)

        evidence_types = {e.evidence_type for e in bundle.evidence}
        self.assertIn(EvidenceType.CODE, evidence_types)
        self.assertIn(EvidenceType.TEST, evidence_types)

        code_evs = [e for e in bundle.evidence if e.evidence_type == EvidenceType.CODE]
        test_evs = [e for e in bundle.evidence if e.evidence_type == EvidenceType.TEST]
        self.assertEqual(len(bundle.evidence), len(code_evs) + len(test_evs))
        self.assertEqual(len(code_evs), 1)
        self.assertEqual(len(test_evs), 1)
        self.assertEqual(test_evs[0].symbol, "test_authenticate_user_valid")

    def test_orchestration_test_evidence_ids_follow_code_evidence_ids_without_collisions(self):
        """Test evidence IDs continue after code evidence IDs sequentially to prevent collisions."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        t1 = self._make_test_record(test_id="TEST-1", criterion_ids=["AC-1"])
        t2 = self._make_test_record(test_id="TEST-2", test_name="test_audit_event", criterion_ids=["AC-2"])
        pipeline_input = self._make_pipeline_input(test_results=[t1, t2])

        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        # Invariant: all evidence IDs are strictly unique
        all_ids = [e.evidence_id for e in bundle.evidence]
        self.assertEqual(len(all_ids), len(set(all_ids)))

        code_ids = [e.evidence_id for e in bundle.evidence if e.evidence_type == EvidenceType.CODE]
        test_ids = [e.evidence_id for e in bundle.evidence if e.evidence_type == EvidenceType.TEST]
        self.assertGreaterEqual(len(code_ids), 1)
        self.assertEqual(len(test_ids), 2)

        # The first test evidence ID must be exactly the next integer after the last code evidence ID
        last_code_num = int(code_ids[-1].split("-")[1])
        first_test_num = int(test_ids[0].split("-")[1])
        self.assertEqual(first_test_num, last_code_num + 1)
        self.assertEqual(test_ids, [f"EV-{last_code_num + 1}", f"EV-{last_code_num + 2}"])

    def test_orchestration_passing_test_with_matching_head_commit_produces_verified(self):
        """A valid linked passing test with reliable matching head-commit provenance produces VERIFIED status."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertIn("verified", ac1_finding.title.lower())
        self.assertIn("passing test execution(s)", ac1_finding.explanation.lower())
        self.assertIn("279ee818", ac1_finding.explanation)

    def test_orchestration_passing_test_without_code_evidence_cannot_produce_verified(self):
        """A passing test with matching head commit cannot produce VERIFIED if there is no supporting code evidence."""
        # Using self.orchestrator which has default CodeInvestigationService (yielding 0 observations for AC-1)
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = self.orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("no supporting code implementation evidence", ac1_finding.explanation.lower())

    def test_orchestration_missing_head_commit_provenance_fails_closed_to_partially_verified(self):
        """When PR head commit provenance is not explicitly provided, orchestrator fails closed (cannot produce VERIFIED)."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        # Call orchestrate() without head_commit_sha; PullRequest model does not have contractual head_sha
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(pipeline_input)

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("head commit sha is not provided", ac1_finding.explanation.lower())

    def test_orchestration_stale_commit_sha_cannot_produce_verified(self):
        """Test record executed against a stale/mismatched commit SHA cannot produce VERIFIED status."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="1111111111111111111111111111111111111111",  # Stale SHA
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertIn("test commit provenance cannot be verified", ac1_finding.explanation.lower())

    def test_orchestration_failed_test_produces_requires_review(self):
        """A linked failing test takes precedence over passing code observations and produces REQUIRES_REVIEW."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.FAILED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            failure_message="AssertionError: password validation returned False",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertEqual(ac1_finding.status, FindingStatus.REQUIRES_REVIEW)
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)
        self.assertIn("linked test failure(s)", ac1_finding.explanation.lower())
        self.assertIn("password validation returned False", ac1_finding.explanation)

    def test_orchestration_unlinked_test_does_not_verify_unrelated_criteria(self):
        """A test linked only to AC-2 does not mark AC-1 as VERIFIED."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-2",
            test_name="test_audit_event_logged",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-2"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        # AC-1 has code evidence from login.py but no linked tests -> PARTIALLY_VERIFIED
        self.assertEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)

    def test_orchestration_empty_test_results_preserves_phase1_behavior(self):
        """An empty test_results list preserves Phase 1 behavior: no test evidence in bundle and PARTIALLY_VERIFIED."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        pipeline_input = self._make_pipeline_input(test_results=[])
        bundle = orchestrator.orchestrate(pipeline_input)

        self.assertIsNone(bundle.test_evidence_build_result)
        evidence_types = {e.evidence_type for e in bundle.evidence}
        self.assertIn(EvidenceType.CODE, evidence_types)
        self.assertNotIn(EvidenceType.TEST, evidence_types)

        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        self.assertEqual(ac1_finding.status, FindingStatus.PARTIALLY_VERIFIED)
        self.assertNotEqual(ac1_finding.status, FindingStatus.VERIFIED)

    def test_orchestration_every_finding_evidence_id_exists_in_final_bundle(self):
        """Referential integrity: every evidence ID cited in any finding must exist in bundle.evidence."""
        orchestrator = InvestigationPipelineOrchestrator(
            code_investigator=self._make_mock_code_investigator()
        )
        test_rec = self._make_test_record(
            test_id="TEST-1",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        pipeline_input = self._make_pipeline_input(test_results=[test_rec])
        bundle = orchestrator.orchestrate(
            pipeline_input,
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

        bundle_evidence_ids = {e.evidence_id for e in bundle.evidence}
        for finding in bundle.findings:
            self.assertTrue(set(finding.evidence_ids).issubset(bundle_evidence_ids))

        # AC-1 should cite both CODE and TEST evidence
        ac1_finding = next(f for f in bundle.findings if f.criterion_id == "AC-1")
        cited_ev_types = {
            e.evidence_type for e in bundle.evidence if e.evidence_id in ac1_finding.evidence_ids
        }
        self.assertIn(EvidenceType.CODE, cited_ev_types)
        self.assertIn(EvidenceType.TEST, cited_ev_types)

    def test_orchestrator_dependency_injection_and_backward_compatibility(self):
        """Constructor allows injecting custom TestEvidenceBuilder and preserves backward-compatible defaults."""
        custom_test_builder = MagicMock(spec=TestEvidenceBuilder)
        orchestrator = InvestigationPipelineOrchestrator(test_evidence_builder=custom_test_builder)
        self.assertIs(orchestrator.test_evidence_builder, custom_test_builder)

        # Default constructor instantiates a real TestEvidenceBuilder
        default_orchestrator = InvestigationPipelineOrchestrator()
        self.assertIsInstance(default_orchestrator.test_evidence_builder, TestEvidenceBuilder)

        # Positional compatibility: if 6 positional args are provided, test_evidence_builder is the 6th
        orchestrator_pos = InvestigationPipelineOrchestrator(
            None, None, None, None, None, custom_test_builder
        )
        self.assertIs(orchestrator_pos.test_evidence_builder, custom_test_builder)
