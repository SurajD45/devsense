"""
DevSense — Unit Tests for Test Evidence Schemas
================================================
Focused unit tests for TestExecutionStatus, TestExecutionRecord,
TestEvidenceBuildResult, and their integration with InvestigationPipelineInput
and InvestigationResultBundle.
"""

from datetime import datetime, timezone
import unittest
from pydantic import ValidationError

from app.domain.models import (
    AcceptanceCriterion,
    Evidence,
    EvidenceType,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    PullRequest,
    Requirement,
)
from app.investigation import (
    InvestigationPipelineInput,
    InvestigationResultBundle,
    TestEvidenceBuildResult,
    TestExecutionRecord,
    TestExecutionStatus,
)


class TestTestExecutionStatusEnum(unittest.TestCase):
    """Tests for TestExecutionStatus enum values and behaviors."""

    def test_enum_members_and_values(self):
        self.assertEqual(TestExecutionStatus.PASSED, "PASSED")
        self.assertEqual(TestExecutionStatus.FAILED, "FAILED")
        self.assertEqual(TestExecutionStatus.SKIPPED, "SKIPPED")
        self.assertEqual(TestExecutionStatus.NOT_RUN, "NOT_RUN")
        self.assertEqual(
            {s.value for s in TestExecutionStatus},
            {"PASSED", "FAILED", "SKIPPED", "NOT_RUN"},
        )

    def test_str_subclass_behavior(self):
        self.assertIsInstance(TestExecutionStatus.PASSED, str)
        self.assertEqual(TestExecutionStatus.PASSED, "PASSED")
        self.assertEqual(TestExecutionStatus.PASSED.value, "PASSED")


class TestTestExecutionRecordSchema(unittest.TestCase):
    """Tests for TestExecutionRecord model and validators."""

    def _make_record(self, **overrides) -> TestExecutionRecord:
        defaults = dict(
            test_id="tests/test_auth.py::test_user_credential_validation",
            file_path="tests/test_auth.py",
            test_name="test_user_credential_validation",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            duration_ms=45.2,
            failure_message=None,
            start_line=10,
            end_line=25,
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )
        defaults.update(overrides)
        return TestExecutionRecord(**defaults)

    def test_valid_record_full(self):
        rec = self._make_record()
        self.assertEqual(rec.test_id, "tests/test_auth.py::test_user_credential_validation")
        self.assertEqual(rec.file_path, "tests/test_auth.py")
        self.assertEqual(rec.test_name, "test_user_credential_validation")
        self.assertEqual(rec.status, TestExecutionStatus.PASSED)
        self.assertEqual(rec.criterion_ids, ["AC-1"])
        self.assertEqual(rec.duration_ms, 45.2)
        self.assertIsNone(rec.failure_message)
        self.assertEqual(rec.start_line, 10)
        self.assertEqual(rec.end_line, 25)
        self.assertEqual(rec.commit_sha, "279ee818dad7f588a305372781ee3a4a347d3c9a")

    def test_valid_record_minimal_defaults(self):
        rec = TestExecutionRecord(
            test_id="test_suite.py::test_minimal",
            file_path="test_suite.py",
            test_name="test_minimal",
            status=TestExecutionStatus.FAILED,
        )
        self.assertEqual(rec.criterion_ids, [])
        self.assertIsNone(rec.duration_ms)
        self.assertIsNone(rec.failure_message)
        self.assertIsNone(rec.start_line)
        self.assertIsNone(rec.end_line)
        self.assertIsNone(rec.commit_sha)

    def test_reject_empty_or_whitespace_identifiers(self):
        with self.assertRaises(ValidationError) as ctx:
            self._make_record(test_id="")
        self.assertIn("test_id cannot be empty or whitespace-only", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(test_id="   ")
        self.assertIn("test_id cannot be empty or whitespace-only", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(file_path="")
        self.assertIn("file_path cannot be empty or whitespace-only", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(file_path=" \t ")
        self.assertIn("file_path cannot be empty or whitespace-only", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(test_name="")
        self.assertIn("test_name cannot be empty or whitespace-only", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(test_name=" \n ")
        self.assertIn("test_name cannot be empty or whitespace-only", str(ctx.exception))

    def test_criterion_ids_validation_and_deduplication(self):
        # Valid multiple criteria with duplicate
        rec = self._make_record(criterion_ids=["AC-1", "AC-2", "AC-1", "AC-42"])
        self.assertEqual(rec.criterion_ids, ["AC-1", "AC-2", "AC-42"])

        # Invalid criterion patterns
        invalid_ids = ["AC", "ac-1", "AC-", "AC-abc", "CRITERION-1", "1"]
        for bad_id in invalid_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValidationError) as ctx:
                    self._make_record(criterion_ids=[bad_id])
                self.assertIn("must match pattern 'AC-<number>'", str(ctx.exception))

        empty_ids = ["", "   "]
        for empty_id in empty_ids:
            with self.subTest(empty_id=empty_id):
                with self.assertRaises(ValidationError) as ctx:
                    self._make_record(criterion_ids=[empty_id])
                self.assertIn("cannot be empty or whitespace-only", str(ctx.exception))

    def test_duration_ms_validation(self):
        rec_zero = self._make_record(duration_ms=0)
        self.assertEqual(rec_zero.duration_ms, 0.0)

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(duration_ms=-1.0)
        self.assertIn("duration_ms", str(ctx.exception))

    def test_failure_message_handling(self):
        rec_with_msg = self._make_record(failure_message="  AssertionError: Expected 200 OK  ")
        self.assertEqual(rec_with_msg.failure_message, "AssertionError: Expected 200 OK")

        rec_whitespace_only = self._make_record(failure_message="   \n\t  ")
        self.assertIsNone(rec_whitespace_only.failure_message)

    def test_line_range_validation(self):
        # start_line and end_line >= 1
        with self.assertRaises(ValidationError) as ctx:
            self._make_record(start_line=0)
        self.assertIn("start_line", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            self._make_record(end_line=0)
        self.assertIn("end_line", str(ctx.exception))

        # end_line < start_line rejected
        with self.assertRaises(ValidationError) as ctx:
            self._make_record(start_line=50, end_line=40)
        self.assertIn("end_line (40) must be >= start_line (50)", str(ctx.exception))

        # end_line == start_line allowed
        rec = self._make_record(start_line=20, end_line=20)
        self.assertEqual(rec.start_line, 20)
        self.assertEqual(rec.end_line, 20)

    def test_commit_sha_validation(self):
        # Valid short SHA (7 hex chars)
        rec_short = self._make_record(commit_sha="279ee81")
        self.assertEqual(rec_short.commit_sha, "279ee81")

        # Valid 40-char SHA with uppercase converted to lowercase
        rec_upper = self._make_record(commit_sha="279EE818DAD7F588A305372781EE3A4A347D3C9A")
        self.assertEqual(rec_upper.commit_sha, "279ee818dad7f588a305372781ee3a4a347d3c9a")

        # Invalid SHAs
        invalid_shas = ["123456", "not_a_hex_sha", "g" * 40, "   "]
        for bad_sha in invalid_shas:
            with self.subTest(bad_sha=bad_sha):
                with self.assertRaises(ValidationError) as ctx:
                    self._make_record(commit_sha=bad_sha)
                self.assertIn("commit_sha", str(ctx.exception))


class TestTestEvidenceBuildResultSchema(unittest.TestCase):
    """Tests for TestEvidenceBuildResult model and traceability validation."""

    def _make_evidence(self, ev_id: str = "EV-1", test_name: str = "test_auth") -> Evidence:
        return Evidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.TEST,
            repository="SurajD45/devsense",
            file_path="tests/test_auth.py",
            start_line=10,
            end_line=25,
            symbol=test_name,
            commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
            description=f"Automated test {test_name} passed.",
        )

    def _make_test_record(self, test_id: str = "t1", name: str = "test_auth") -> TestExecutionRecord:
        return TestExecutionRecord(
            test_id=test_id,
            file_path="tests/test_auth.py",
            test_name=name,
            status=TestExecutionStatus.PASSED,
        )

    def test_empty_defaults(self):
        res = TestEvidenceBuildResult()
        self.assertEqual(res.evidence, [])
        self.assertEqual(res.test_to_evidence, {})
        self.assertEqual(res.test_records, [])
        self.assertEqual(len(res), 0)
        self.assertEqual(list(res), [])

    def test_valid_traceability_round_trip(self):
        ev1 = self._make_evidence("EV-1", "test_auth")
        ev2 = self._make_evidence("EV-2", "test_audit")
        rec1 = self._make_test_record("t1", "test_auth")
        rec2 = self._make_test_record("t2", "test_audit")

        res = TestEvidenceBuildResult(
            evidence=[ev1, ev2],
            test_to_evidence={"t1": "EV-1", "t2": "EV-2"},
            test_records=[rec1, rec2],
        )

        self.assertEqual(len(res), 2)
        self.assertEqual(res[0].evidence_id, "EV-1")
        self.assertEqual(res.evidence_to_test, {"EV-1": "t1", "EV-2": "t2"})

    def test_reject_duplicate_evidence_or_test_ids(self):
        ev1 = self._make_evidence("EV-1")
        ev1_dup = self._make_evidence("EV-1")
        rec1 = self._make_test_record("t1")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1, ev1_dup],
                test_to_evidence={"t1": "EV-1"},
                test_records=[rec1],
            )
        self.assertIn("Duplicate evidence_id 'EV-1'", str(ctx.exception))

        rec1_dup = self._make_test_record("t1")
        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1],
                test_to_evidence={"t1": "EV-1"},
                test_records=[rec1, rec1_dup],
            )
        self.assertIn("Duplicate test_id 't1'", str(ctx.exception))

    def test_reject_mapping_referencing_unknown_test_record(self):
        ev1 = self._make_evidence("EV-1")
        rec1 = self._make_test_record("t1")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1],
                test_to_evidence={"unknown_test_id": "EV-1"},
                test_records=[rec1],
            )
        self.assertIn("test_to_evidence references unknown test_id(s) not in test_records", str(ctx.exception))

    def test_reject_mapping_referencing_unknown_evidence_id(self):
        ev1 = self._make_evidence("EV-1")
        rec1 = self._make_test_record("t1")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1],
                test_to_evidence={"t1": "EV-999"},
                test_records=[rec1],
            )
        self.assertIn("Evidence IDs in evidence list do not match IDs in test_to_evidence mapping", str(ctx.exception))

    def test_reject_mismatched_evidence_and_mapping_lengths(self):
        ev1 = self._make_evidence("EV-1")
        ev2 = self._make_evidence("EV-2")
        rec1 = self._make_test_record("t1")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1, ev2],
                test_to_evidence={"t1": "EV-1"},
                test_records=[rec1],
            )
        self.assertIn("Evidence IDs in evidence list do not match IDs in test_to_evidence mapping", str(ctx.exception))

    def test_reject_test_record_missing_from_mapping(self):
        ev1 = self._make_evidence("EV-1")
        rec1 = self._make_test_record("t1")
        rec2 = self._make_test_record("t2")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1],
                test_to_evidence={"t1": "EV-1"},
                test_records=[rec1, rec2],
            )
        self.assertIn("test_records contains test_id(s) missing from test_to_evidence mapping", str(ctx.exception))

    def test_reject_duplicate_mapping_values(self):
        ev1 = self._make_evidence("EV-1")
        rec1 = self._make_test_record("t1")
        rec2 = self._make_test_record("t2")

        with self.assertRaises(ValidationError) as ctx:
            TestEvidenceBuildResult(
                evidence=[ev1],
                test_to_evidence={"t1": "EV-1", "t2": "EV-1"},
                test_records=[rec1, rec2],
            )
        self.assertIn("Mismatch between number of evidence objects", str(ctx.exception))


class TestPipelineInputAndBundleIntegration(unittest.TestCase):
    """Tests for InvestigationPipelineInput and InvestigationResultBundle with test schemas."""

    def _make_criterion(self, cid: str = "AC-1") -> AcceptanceCriterion:
        return AcceptanceCriterion(
            criterion_id=cid,
            title="Criterion title",
            description="Criterion description",
            is_met=False,
        )

    def _make_requirement(self) -> Requirement:
        return Requirement(
            requirement_id="REQ-1",
            title="Requirement title",
            description="Requirement description",
            status="Active",
            acceptance_criteria=[self._make_criterion()],
        )

    def _make_pr(self) -> PullRequest:
        return PullRequest(
            repository="SurajD45/devsense",
            number=42,
            title="PR Title",
            description="PR Description",
            author="suraj",
            source_branch="feature/auth",
            target_branch="main",
            state="open",
            head_commit_sha="279ee818dad7f588a305372781ee3a4a347d3c9a",
        )

    def _make_investigation(self, finding_ids: list[str]) -> Investigation:
        return Investigation(
            investigation_id="INV-42",
            pull_request_number=42,
            requirement_id="REQ-1",
            status=InvestigationStatus.COMPLETED,
            finding_ids=finding_ids,
            created_at=datetime.now(timezone.utc),
        )

    def test_pipeline_input_defaults_test_results_to_empty_list(self):
        inp = InvestigationPipelineInput(
            pull_request=self._make_pr(),
            requirement=self._make_requirement(),
        )
        self.assertEqual(inp.test_results, [])

    def test_pipeline_input_with_valid_test_results(self):
        rec1 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_login",
            status=TestExecutionStatus.PASSED,
        )
        rec2 = TestExecutionRecord(
            test_id="t2",
            file_path="tests/test_auth.py",
            test_name="test_logout",
            status=TestExecutionStatus.PASSED,
        )
        inp = InvestigationPipelineInput(
            pull_request=self._make_pr(),
            requirement=self._make_requirement(),
            test_results=[rec1, rec2],
        )
        self.assertEqual(len(inp.test_results), 2)
        self.assertEqual(inp.test_results[0].test_id, "t1")

    def test_pipeline_input_rejects_duplicate_test_ids(self):
        rec1 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_login",
            status=TestExecutionStatus.PASSED,
        )
        rec2 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_duplicate",
            status=TestExecutionStatus.FAILED,
        )
        with self.assertRaises(ValidationError) as ctx:
            InvestigationPipelineInput(
                pull_request=self._make_pr(),
                requirement=self._make_requirement(),
                test_results=[rec1, rec2],
            )
        self.assertIn("Duplicate test_id 't1' in test_results", str(ctx.exception))

    def test_bundle_with_optional_test_evidence_build_result(self):
        ev1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.TEST,
            repository="SurajD45/devsense",
            file_path="tests/test_auth.py",
            symbol="test_login",
            description="Test passed",
        )
        rec1 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_login",
            status=TestExecutionStatus.PASSED,
        )
        test_build = TestEvidenceBuildResult(
            evidence=[ev1],
            test_to_evidence={"t1": "EV-1"},
            test_records=[rec1],
        )
        finding = Finding(
            finding_id="FIND-1",
            criterion_id="AC-1",
            status=FindingStatus.VERIFIED,
            title="Verified: user authentication",
            explanation="Test passed with complete verification.",
            evidence_ids=["EV-1"],
        )
        bundle = InvestigationResultBundle(
            investigation=self._make_investigation(finding_ids=["FIND-1"]),
            findings=[finding],
            evidence=[ev1],
            test_evidence_build_result=test_build,
        )
        self.assertIsNotNone(bundle.test_evidence_build_result)
        self.assertEqual(bundle.test_evidence_build_result.evidence[0].evidence_id, "EV-1")

    def test_bundle_rejects_missing_evidence_ids_from_test_evidence_build_result(self):
        ev1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.TEST,
            repository="SurajD45/devsense",
            file_path="tests/test_auth.py",
            symbol="test_login",
            description="Test passed",
        )
        ev2 = Evidence(
            evidence_id="EV-2",
            evidence_type=EvidenceType.TEST,
            repository="SurajD45/devsense",
            file_path="tests/test_auth.py",
            symbol="test_logout",
            description="Test passed",
        )
        rec1 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_login",
            status=TestExecutionStatus.PASSED,
        )
        rec2 = TestExecutionRecord(
            test_id="t2",
            file_path="tests/test_auth.py",
            test_name="test_logout",
            status=TestExecutionStatus.PASSED,
        )
        test_build = TestEvidenceBuildResult(
            evidence=[ev1, ev2],
            test_to_evidence={"t1": "EV-1", "t2": "EV-2"},
            test_records=[rec1, rec2],
        )
        finding = Finding(
            finding_id="FIND-1",
            criterion_id="AC-1",
            status=FindingStatus.PARTIALLY_VERIFIED,
            title="Criterion",
            explanation="Explanation",
            evidence_ids=["EV-1"],
        )
        # Bundle evidence only contains EV-1, missing EV-2 from test_build
        with self.assertRaises(ValidationError) as ctx:
            InvestigationResultBundle(
                investigation=self._make_investigation(finding_ids=["FIND-1"]),
                findings=[finding],
                evidence=[ev1],
                test_evidence_build_result=test_build,
            )
        self.assertIn("missing evidence IDs from test_evidence_build_result", str(ctx.exception))

    def test_bundle_serialization_round_trip_with_test_evidence(self):
        ev1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.TEST,
            repository="SurajD45/devsense",
            file_path="tests/test_auth.py",
            symbol="test_login",
            description="Test passed",
        )
        rec1 = TestExecutionRecord(
            test_id="t1",
            file_path="tests/test_auth.py",
            test_name="test_login",
            status=TestExecutionStatus.PASSED,
            criterion_ids=["AC-1"],
            duration_ms=25.0,
        )
        test_build = TestEvidenceBuildResult(
            evidence=[ev1],
            test_to_evidence={"t1": "EV-1"},
            test_records=[rec1],
        )
        finding = Finding(
            finding_id="FIND-1",
            criterion_id="AC-1",
            status=FindingStatus.VERIFIED,
            title="Verified: login",
            explanation="Verified by test t1",
            evidence_ids=["EV-1"],
        )
        bundle = InvestigationResultBundle(
            investigation=self._make_investigation(finding_ids=["FIND-1"]),
            findings=[finding],
            evidence=[ev1],
            test_evidence_build_result=test_build,
        )

        dumped_json = bundle.model_dump_json()
        reloaded = InvestigationResultBundle.model_validate_json(dumped_json)
        self.assertIsNotNone(reloaded.test_evidence_build_result)
        self.assertEqual(len(reloaded.test_evidence_build_result.test_records), 1)
        self.assertEqual(reloaded.test_evidence_build_result.test_records[0].test_id, "t1")
        self.assertEqual(reloaded.test_evidence_build_result.test_records[0].status, TestExecutionStatus.PASSED)


if __name__ == "__main__":
    unittest.main()
