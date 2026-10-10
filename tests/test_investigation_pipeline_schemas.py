"""
DevSense — Investigation Pipeline Schemas Tests
================================================
Comprehensive unit test suite for InvestigationPipelineInput and InvestigationResultBundle.
"""

from datetime import datetime, timezone
import unittest
from pydantic import ValidationError

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
)


class BasePipelineSchemaTestCase(unittest.TestCase):
    """Shared helpers for constructing valid domain fixtures."""

    def _make_criterion(self, cid: str = "AC-1", title: str = "User can login") -> AcceptanceCriterion:
        return AcceptanceCriterion(
            criterion_id=cid,
            title=title,
            description="Detailed criterion description.",
            is_met=False,
        )

    def _make_requirement(self, req_id: str = "REQ-1", criteria: list[AcceptanceCriterion] | None = None) -> Requirement:
        if criteria is None:
            criteria = [self._make_criterion()]
        return Requirement(
            requirement_id=req_id,
            title="Authentication Feature",
            description="Require email and password login.",
            status="Active",
            acceptance_criteria=criteria,
        )

    def _make_pull_request(self, pr_num: int = 101) -> PullRequest:
        return PullRequest(
            repository="acme/platform",
            number=pr_num,
            title="Implement user authentication",
            description="Adds login endpoints and services.",
            author="alice",
            source_branch="feature/auth",
            target_branch="main",
            state="open",
            commits=[
                Commit(sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", message="initial commit", author="alice")
            ],
            changed_files=[
                ChangedFile(path="app/auth.py", status="modified", additions=50, deletions=5)
            ],
        )

    def _make_evidence(self, ev_id: str = "EV-1", file_path: str = "app/auth.py") -> Evidence:
        return Evidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.CODE,
            repository="acme/platform",
            file_path=file_path,
            start_line=10,
            end_line=20,
            symbol="authenticate_user",
            commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            description="Authentication verification logic",
        )

    def _make_finding(
        self,
        finding_id: str = "FIND-1",
        criterion_id: str = "AC-1",
        status: FindingStatus = FindingStatus.VERIFIED,
        evidence_ids: list[str] | None = None,
    ) -> Finding:
        if evidence_ids is None:
            evidence_ids = ["EV-1"]
        return Finding(
            finding_id=finding_id,
            criterion_id=criterion_id,
            status=status,
            title="Password verification implemented",
            explanation="The login handler properly hashes and verifies passwords.",
            evidence_ids=evidence_ids,
        )

    def _make_investigation(
        self,
        inv_id: str = "INV-1",
        pr_number: int = 101,
        req_id: str = "REQ-1",
        status: InvestigationStatus = InvestigationStatus.COMPLETED,
        finding_ids: list[str] | None = None,
    ) -> Investigation:
        if finding_ids is None:
            finding_ids = ["FIND-1"]
        return Investigation(
            investigation_id=inv_id,
            pull_request_number=pr_number,
            requirement_id=req_id,
            status=status,
            finding_ids=finding_ids,
            created_at=datetime.now(timezone.utc),
        )


class TestInvestigationPipelineInput(BasePipelineSchemaTestCase):
    """Unit tests for InvestigationPipelineInput contract and validation."""

    def test_valid_minimal_construction(self):
        pr = self._make_pull_request()
        req = self._make_requirement()
        pipeline_input = InvestigationPipelineInput(
            pull_request=pr,
            requirement=req,
        )
        self.assertEqual(pipeline_input.pull_request.number, 101)
        self.assertEqual(pipeline_input.requirement.requirement_id, "REQ-1")
        self.assertEqual(pipeline_input.file_contents, {})
        self.assertIsNone(pipeline_input.investigation_id)

    def test_valid_full_construction(self):
        pr = self._make_pull_request()
        req = self._make_requirement()
        file_contents = {"app/auth.py": "def authenticate(): pass"}
        pipeline_input = InvestigationPipelineInput(
            pull_request=pr,
            requirement=req,
            file_contents=file_contents,
            investigation_id="INV-42",
        )
        self.assertEqual(pipeline_input.investigation_id, "INV-42")
        self.assertEqual(pipeline_input.file_contents["app/auth.py"], "def authenticate(): pass")

    def test_independent_default_collections(self):
        in1 = InvestigationPipelineInput(pull_request=self._make_pull_request(), requirement=self._make_requirement())
        in2 = InvestigationPipelineInput(pull_request=self._make_pull_request(), requirement=self._make_requirement())
        in1.file_contents["test.py"] = "content"
        self.assertNotIn("test.py", in2.file_contents)

    def test_investigation_id_pattern_validation(self):
        pr = self._make_pull_request()
        req = self._make_requirement()

        valid_ids = ["INV-1", "INV-10", "INV-999"]
        for vid in valid_ids:
            inp = InvestigationPipelineInput(pull_request=pr, requirement=req, investigation_id=vid)
            self.assertEqual(inp.investigation_id, vid)

        # Whitespace stripping
        inp = InvestigationPipelineInput(pull_request=pr, requirement=req, investigation_id="  INV-7  ")
        self.assertEqual(inp.investigation_id, "INV-7")

        invalid_ids = ["inv-1", "INV1", "INV-", "INV-abc", "AC-1", "FIND-1", "   "]
        for iid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Should reject: {iid}"):
                InvestigationPipelineInput(pull_request=pr, requirement=req, investigation_id=iid)

    def test_rejection_when_requirement_has_empty_acceptance_criteria(self):
        pr = self._make_pull_request()
        req_without_criteria = Requirement(
            requirement_id="REQ-EMPTY",
            title="Empty criteria",
            status="Draft",
            acceptance_criteria=[],
        )
        with self.assertRaises(ValidationError) as ctx:
            InvestigationPipelineInput(pull_request=pr, requirement=req_without_criteria)
        self.assertIn("at least one AcceptanceCriterion", str(ctx.exception))

    def test_rejection_when_missing_required_fields(self):
        pr = self._make_pull_request()
        req = self._make_requirement()

        with self.assertRaises(ValidationError):
            InvestigationPipelineInput(requirement=req)  # missing pull_request

        with self.assertRaises(ValidationError):
            InvestigationPipelineInput(pull_request=pr)  # missing requirement

    def test_file_contents_validation(self):
        pr = self._make_pull_request()
        req = self._make_requirement()

        # Whitespace key stripping
        inp = InvestigationPipelineInput(
            pull_request=pr,
            requirement=req,
            file_contents={"  app/service.py  ": "code"},
        )
        self.assertIn("app/service.py", inp.file_contents)

        # Empty file path key rejected
        with self.assertRaises(ValidationError):
            InvestigationPipelineInput(
                pull_request=pr,
                requirement=req,
                file_contents={"   ": "code"},
            )

        # Non-string content rejected
        with self.assertRaises(ValidationError):
            InvestigationPipelineInput(
                pull_request=pr,
                requirement=req,
                file_contents={"app/auth.py": 123},  # type: ignore
            )

    def test_file_contents_colliding_keys_rejected(self):
        pr = self._make_pull_request()
        req = self._make_requirement()
        with self.assertRaises(ValidationError) as ctx:
            InvestigationPipelineInput(
                pull_request=pr,
                requirement=req,
                file_contents={"app/auth.py": "code1", "  app/auth.py  ": "code2"},
            )
        self.assertIn("Duplicate or colliding file path", str(ctx.exception))

    def test_no_mutation_of_caller_objects(self):
        pr = self._make_pull_request()
        req = self._make_requirement()
        raw_files = {"app/auth.py": "print('hello')"}
        raw_files_copy = dict(raw_files)

        inp = InvestigationPipelineInput(pull_request=pr, requirement=req, file_contents=raw_files)
        inp.file_contents["app/new.py"] = "new"

        self.assertEqual(raw_files, raw_files_copy)
        self.assertNotIn("app/new.py", raw_files)

    def test_serialization_round_trip(self):
        pr = self._make_pull_request()
        req = self._make_requirement()
        inp = InvestigationPipelineInput(
            pull_request=pr,
            requirement=req,
            file_contents={"app/auth.py": "code"},
            investigation_id="INV-10",
        )
        dumped = inp.model_dump()
        self.assertEqual(dumped["investigation_id"], "INV-10")
        self.assertEqual(dumped["pull_request"]["number"], 101)
        self.assertEqual(dumped["requirement"]["requirement_id"], "REQ-1")

        json_str = inp.model_dump_json()
        reloaded = InvestigationPipelineInput.model_validate_json(json_str)
        self.assertEqual(reloaded.investigation_id, "INV-10")
        self.assertEqual(reloaded.pull_request.number, 101)


class TestInvestigationResultBundle(BasePipelineSchemaTestCase):
    """Unit tests for InvestigationResultBundle contract, integrity, and failure handling."""

    def test_valid_completed_investigation_with_findings(self):
        ev = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-1", finding_ids=["FIND-1"])

        bundle = InvestigationResultBundle(
            investigation=inv,
            findings=[fnd],
            evidence=[ev],
        )

        self.assertEqual(bundle.investigation_id, "INV-1")
        self.assertTrue(bundle.is_successful)
        self.assertEqual(len(bundle.findings), 1)
        self.assertEqual(len(bundle.evidence), 1)
        self.assertEqual(bundle.errors, [])

    def test_valid_completed_investigation_with_zero_findings_permitted(self):
        inv = self._make_investigation(
            "INV-2",
            status=InvestigationStatus.COMPLETED,
            finding_ids=[],
        )
        bundle = InvestigationResultBundle(
            investigation=inv,
            findings=[],
            evidence=[],
        )
        self.assertEqual(bundle.investigation_id, "INV-2")
        self.assertTrue(bundle.is_successful)
        self.assertEqual(len(bundle.findings), 0)
        self.assertEqual(len(bundle.evidence), 0)

    def test_valid_failed_investigation_with_errors(self):
        inv = self._make_investigation(
            "INV-3",
            status=InvestigationStatus.FAILED,
            finding_ids=[],
        )
        bundle = InvestigationResultBundle(
            investigation=inv,
            findings=[],
            evidence=[],
            errors=["Network timeout while analyzing AST"],
        )
        self.assertEqual(bundle.investigation_id, "INV-3")
        self.assertFalse(bundle.is_successful)
        self.assertEqual(bundle.errors, ["Network timeout while analyzing AST"])

    def test_failed_investigation_requires_non_empty_errors(self):
        inv = self._make_investigation(
            "INV-4",
            status=InvestigationStatus.FAILED,
            finding_ids=[],
        )
        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[],
                evidence=[],
                errors=[],
            )
        self.assertIn("must include at least one error message", str(ctx.exception))

    def test_errors_whitespace_stripped_and_empty_rejected(self):
        inv = self._make_investigation("INV-5", status=InvestigationStatus.FAILED, finding_ids=[])

        # Whitespace stripping
        bundle = InvestigationResultBundle(
            investigation=inv,
            errors=["  fatal failure  "],
        )
        self.assertEqual(bundle.errors, ["fatal failure"])

        # Whitespace-only string rejected
        with self.assertRaises(ValidationError):
            InvestigationResultBundle(
                investigation=inv,
                errors=["   "],
            )

    def test_mismatched_finding_ids_between_investigation_and_findings_list(self):
        ev = self._make_evidence("EV-1")
        fnd1 = self._make_finding("FIND-1", evidence_ids=["EV-1"])

        # Investigation lists FIND-2, but bundle contains FIND-1
        inv = self._make_investigation("INV-6", finding_ids=["FIND-2"])
        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd1],
                evidence=[ev],
            )
        self.assertIn("Mismatch between investigation.finding_ids", str(ctx.exception))

    def test_missing_finding_ids_in_findings_list(self):
        ev = self._make_evidence("EV-1")
        fnd1 = self._make_finding("FIND-1", evidence_ids=["EV-1"])

        # Investigation lists 2 findings, but only 1 supplied in findings list
        inv = self._make_investigation("INV-7", finding_ids=["FIND-1", "FIND-2"])
        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd1],
                evidence=[ev],
            )
        self.assertIn("Mismatch between investigation.finding_ids", str(ctx.exception))

    def test_referential_integrity_unknown_evidence_in_finding_rejected(self):
        # Finding references EV-99, which is not provided in bundle.evidence
        ev = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-99"])
        inv = self._make_investigation("INV-8", finding_ids=["FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd],
                evidence=[ev],
            )
        self.assertIn("references unknown evidence_id 'EV-99'", str(ctx.exception))

    def test_duplicate_finding_ids_rejected(self):
        ev = self._make_evidence("EV-1")
        fnd1 = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        fnd2 = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-9", finding_ids=["FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd1, fnd2],
                evidence=[ev],
            )
        self.assertIn("Duplicate finding_id 'FIND-1'", str(ctx.exception))

    def test_duplicate_evidence_ids_rejected(self):
        ev1 = self._make_evidence("EV-1")
        ev2 = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-10", finding_ids=["FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd],
                evidence=[ev1, ev2],
            )
        self.assertIn("Duplicate evidence_id 'EV-1'", str(ctx.exception))

    def test_duplicate_finding_ids_in_investigation_rejected(self):
        ev = self._make_evidence("EV-1")
        fnd1 = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-101", finding_ids=["FIND-1", "FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd1],
                evidence=[ev],
            )
        self.assertIn("Duplicate finding_ids found in investigation.finding_ids", str(ctx.exception))

    def test_order_mismatch_between_investigation_and_findings_rejected(self):
        ev1 = self._make_evidence("EV-1")
        ev2 = self._make_evidence("EV-2")
        fnd1 = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        fnd2 = self._make_finding("FIND-2", evidence_ids=["EV-2"])
        # investigation has ["FIND-2", "FIND-1"], but findings list is [fnd1, fnd2]
        inv = self._make_investigation("INV-102", finding_ids=["FIND-2", "FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd1, fnd2],
                evidence=[ev1, ev2],
            )
        self.assertIn("Order mismatch between investigation.finding_ids", str(ctx.exception))

    def test_duplicate_evidence_ids_in_single_finding_rejected(self):
        ev = self._make_evidence("EV-1")
        # Finding has duplicate evidence_ids ["EV-1", "EV-1"]
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1", "EV-1"])
        inv = self._make_investigation("INV-103", finding_ids=["FIND-1"])

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd],
                evidence=[ev],
            )
        self.assertIn("contains duplicate evidence_ids", str(ctx.exception))

    def test_duplicate_file_paths_in_code_investigation_results_rejected(self):
        ev = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-104", finding_ids=["FIND-1"])

        code_res1 = CodeInvestigationResult(
            file_path="app/auth.py",
            observations=[
                CodeObservation(
                    observation_id="CODE-1",
                    file_path="app/auth.py",
                    description="Observation 1",
                    observation_type=CodeObservationType.LOGIC,
                )
            ],
        )
        code_res2 = CodeInvestigationResult(
            file_path="app/auth.py",
            observations=[
                CodeObservation(
                    observation_id="CODE-2",
                    file_path="app/auth.py",
                    description="Observation 2",
                    observation_type=CodeObservationType.LOGIC,
                )
            ],
        )

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd],
                evidence=[ev],
                code_investigation_results=[code_res1, code_res2],
            )
        self.assertIn("Duplicate file_path 'app/auth.py'", str(ctx.exception))

    def test_optional_intermediate_artifacts_supported(self):
        ev = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-11", finding_ids=["FIND-1"])

        req_analysis = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Auth summary",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Valid credentials allow access",
                    verification_points=["login endpoint succeeds"],
                    potential_areas=["auth.service"],
                )
            ],
        )

        impact_analysis = ChangeImpactAnalysis(
            classifications=[
                ChangedFileClassification(
                    file_path="app/auth.py",
                    classification=FileClassification.PRIMARY,
                    reasons=["Contains login implementation"],
                )
            ]
        )

        promotion_analysis = FilePromotionAnalysis(
            files=[
                FileReviewItem(
                    file_path="app/auth.py",
                    priority=FileReviewPriority.PRIMARY,
                    reasons=["Direct auth file"],
                    original_classification=FileClassification.PRIMARY,
                )
            ]
        )

        code_results = [
            CodeInvestigationResult(
                file_path="app/auth.py",
                observations=[
                    CodeObservation(
                        observation_id="CODE-1",
                        file_path="app/auth.py",
                        description="Password hash call",
                        observation_type=CodeObservationType.SECURITY,
                        criterion_id="AC-1",
                    )
                ],
            )
        ]

        ev_build_result = CodeEvidenceBuildResult(
            evidence=[ev],
            observation_to_evidence={"CODE-1": "EV-1"},
        )

        bundle = InvestigationResultBundle(
            investigation=inv,
            findings=[fnd],
            evidence=[ev],
            requirement_analysis=req_analysis,
            impact_analysis=impact_analysis,
            promotion_analysis=promotion_analysis,
            code_investigation_results=code_results,
            evidence_build_result=ev_build_result,
        )

        self.assertIsNotNone(bundle.requirement_analysis)
        self.assertIsNotNone(bundle.impact_analysis)
        self.assertIsNotNone(bundle.promotion_analysis)
        self.assertEqual(len(bundle.code_investigation_results), 1)
        self.assertIsNotNone(bundle.evidence_build_result)

    def test_evidence_build_result_consistency_check(self):
        ev1 = self._make_evidence("EV-1")
        ev2 = self._make_evidence("EV-2")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-12", finding_ids=["FIND-1"])

        # evidence_build_result contains EV-2, but bundle evidence only has EV-1
        ev_build_result = CodeEvidenceBuildResult(
            evidence=[ev2],
            observation_to_evidence={"CODE-2": "EV-2"},
        )

        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=inv,
                findings=[fnd],
                evidence=[ev1],
                evidence_build_result=ev_build_result,
            )
        self.assertIn("missing evidence IDs from evidence_build_result", str(ctx.exception))

    def test_independent_default_collections(self):
        inv1 = self._make_investigation("INV-13", finding_ids=[])
        inv2 = self._make_investigation("INV-14", finding_ids=[])

        b1 = InvestigationResultBundle(investigation=inv1)
        b2 = InvestigationResultBundle(investigation=inv2)

        b1.errors.append("test error")
        self.assertNotIn("test error", b2.errors)

    def test_serialization_round_trip(self):
        ev = self._make_evidence("EV-1")
        fnd = self._make_finding("FIND-1", evidence_ids=["EV-1"])
        inv = self._make_investigation("INV-15", finding_ids=["FIND-1"])

        bundle = InvestigationResultBundle(
            investigation=inv,
            findings=[fnd],
            evidence=[ev],
        )

        dumped = bundle.model_dump()
        self.assertEqual(dumped["investigation"]["investigation_id"], "INV-15")
        self.assertEqual(dumped["findings"][0]["finding_id"], "FIND-1")
        self.assertEqual(dumped["evidence"][0]["evidence_id"], "EV-1")

        json_str = bundle.model_dump_json()
        reloaded = InvestigationResultBundle.model_validate_json(json_str)
        self.assertEqual(reloaded.investigation.investigation_id, "INV-15")
        self.assertEqual(len(reloaded.findings), 1)
        self.assertEqual(len(reloaded.evidence), 1)


if __name__ == "__main__":
    unittest.main()
