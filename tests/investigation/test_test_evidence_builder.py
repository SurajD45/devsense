"""
DevSense — Unit Tests for Test Evidence Builder
================================================
Comprehensive unit tests for TestEvidenceBuilder:
- One TestExecutionRecord -> one Evidence with EvidenceType.TEST
- Multiple TestExecutionRecords -> multiple Evidence objects
- Test execution outcomes (PASSED, FAILED, SKIPPED, NOT_RUN)
- Deterministic sequential EV-1, EV-2 generation
- Configurable starting evidence IDs and validation
- Complete test_id -> evidence_id traceability mapping
- Preservation of file_path, test_name/symbol, and line ranges
- Record commit SHA taking precedence over fallback PR head SHA
- Handling of empty input records
- Rejection of invalid inputs (non-list, non-record items, duplicate test IDs)
- Convenience method build_evidence and alias build_result
"""

import unittest
from pydantic import ValidationError

from app.domain.models import Evidence, EvidenceType
from app.investigation.schemas import (
    TestEvidenceBuildResult,
    TestExecutionRecord,
    TestExecutionStatus,
)
from app.investigation.service import TestEvidenceBuilder


class TestTestEvidenceBuilder(unittest.TestCase):
    """Unit tests for TestEvidenceBuilder and resulting Evidence generation."""

    def setUp(self) -> None:
        self.builder = TestEvidenceBuilder()

    def _make_record(
        self,
        test_id: str = "tests/test_auth.py::test_login",
        file_path: str = "tests/test_auth.py",
        test_name: str = "test_login",
        status: TestExecutionStatus = TestExecutionStatus.PASSED,
        criterion_ids: list[str] | None = None,
        duration_ms: float | None = 25.5,
        failure_message: str | None = None,
        start_line: int | None = 10,
        end_line: int | None = 20,
        commit_sha: str | None = None,
    ) -> TestExecutionRecord:
        return TestExecutionRecord(
            test_id=test_id,
            file_path=file_path,
            test_name=test_name,
            status=status,
            criterion_ids=criterion_ids or ["AC-1"],
            duration_ms=duration_ms,
            failure_message=failure_message,
            start_line=start_line,
            end_line=end_line,
            commit_sha=commit_sha,
        )

    # -----------------------------------------------------------------------
    # 1. One record -> one Evidence with EvidenceType.TEST
    # -----------------------------------------------------------------------

    def test_one_record_produces_one_evidence(self) -> None:
        rec = self._make_record()
        result = self.builder.build([rec], repository="SurajD45/devsense")

        self.assertIsInstance(result, TestEvidenceBuildResult)
        self.assertEqual(len(result.evidence), 1)
        ev = result.evidence[0]
        self.assertIsInstance(ev, Evidence)
        self.assertEqual(ev.evidence_type, EvidenceType.TEST)
        self.assertEqual(ev.evidence_id, "EV-1")
        self.assertEqual(ev.repository, "SurajD45/devsense")
        self.assertEqual(ev.file_path, "tests/test_auth.py")
        self.assertEqual(ev.symbol, "test_login")
        self.assertEqual(ev.start_line, 10)
        self.assertEqual(ev.end_line, 20)

    # -----------------------------------------------------------------------
    # 2. Multiple records -> multiple Evidence objects
    # -----------------------------------------------------------------------

    def test_multiple_records_produce_multiple_evidence(self) -> None:
        rec1 = self._make_record("t1", test_name="test_1")
        rec2 = self._make_record("t2", test_name="test_2")
        rec3 = self._make_record("t3", test_name="test_3")

        result = self.builder.build([rec1, rec2, rec3])

        self.assertEqual(len(result.evidence), 3)
        self.assertEqual([e.evidence_id for e in result.evidence], ["EV-1", "EV-2", "EV-3"])
        self.assertEqual([e.symbol for e in result.evidence], ["test_1", "test_2", "test_3"])

    # -----------------------------------------------------------------------
    # 3. Execution outcomes (PASSED, FAILED, SKIPPED, NOT_RUN)
    # -----------------------------------------------------------------------

    def test_outcomes_description_formatting(self) -> None:
        rec_pass = self._make_record(
            "t_pass",
            test_name="test_passed",
            status=TestExecutionStatus.PASSED,
            duration_ms=45.2,
        )
        rec_fail = self._make_record(
            "t_fail",
            test_name="test_failed",
            status=TestExecutionStatus.FAILED,
            duration_ms=12.0,
            failure_message="AssertionError: Expected 200 OK",
        )
        rec_skip = self._make_record(
            "t_skip",
            test_name="test_skipped",
            status=TestExecutionStatus.SKIPPED,
            duration_ms=None,
        )
        rec_not_run = self._make_record(
            "t_not_run",
            test_name="test_unexecuted",
            status=TestExecutionStatus.NOT_RUN,
            duration_ms=None,
        )

        result = self.builder.build([rec_pass, rec_fail, rec_skip, rec_not_run])
        descriptions = [e.description for e in result.evidence]

        self.assertIn("Test 'test_passed' PASSED (45.2ms)", descriptions[0])
        self.assertIn("Test 'test_failed' FAILED (12.0ms): AssertionError: Expected 200 OK", descriptions[1])
        self.assertEqual("Test 'test_skipped' SKIPPED", descriptions[2])
        self.assertEqual("Test 'test_unexecuted' NOT_RUN", descriptions[3])

    # -----------------------------------------------------------------------
    # 4. Evidence IDs and configurable starting IDs
    # -----------------------------------------------------------------------

    def test_deterministic_sequential_evidence_ids(self) -> None:
        records = [self._make_record(f"t_{i}", test_name=f"test_{i}") for i in range(1, 6)]
        result = self.builder.build(records)

        expected_ids = ["EV-1", "EV-2", "EV-3", "EV-4", "EV-5"]
        self.assertEqual([e.evidence_id for e in result.evidence], expected_ids)

    def test_configurable_start_id(self) -> None:
        rec1 = self._make_record("t1")
        rec2 = self._make_record("t2")

        result = self.builder.build([rec1, rec2], start_id=10)
        self.assertEqual([e.evidence_id for e in result.evidence], ["EV-10", "EV-11"])

    def test_invalid_start_id_rejected(self) -> None:
        rec = self._make_record()
        with self.assertRaises(ValueError) as ctx:
            self.builder.build([rec], start_id=0)
        self.assertIn("start_id must be >= 1", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.builder.build([rec], start_id=-5)
        self.assertIn("start_id must be >= 1", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 5. Traceability mapping integrity
    # -----------------------------------------------------------------------

    def test_traceability_mapping_integrity(self) -> None:
        rec1 = self._make_record("tests/test_a.py::test_one")
        rec2 = self._make_record("tests/test_b.py::test_two")

        result = self.builder.build([rec1, rec2])

        self.assertEqual(
            result.test_to_evidence,
            {
                "tests/test_a.py::test_one": "EV-1",
                "tests/test_b.py::test_two": "EV-2",
            },
        )
        self.assertEqual(
            result.evidence_to_test,
            {
                "EV-1": "tests/test_a.py::test_one",
                "EV-2": "tests/test_b.py::test_two",
            },
        )
        self.assertEqual(result.test_records, [rec1, rec2])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].evidence_id, "EV-1")
        self.assertEqual(result[1].evidence_id, "EV-2")

    # -----------------------------------------------------------------------
    # 6. File path, symbol, line range preservation
    # -----------------------------------------------------------------------

    def test_attributes_preservation(self) -> None:
        rec = self._make_record(
            file_path="src/tests/integration/test_payment.py",
            test_name="TestPaymentFlow.test_process_charge",
            start_line=45,
            end_line=75,
        )
        result = self.builder.build([rec])
        ev = result.evidence[0]

        self.assertEqual(ev.file_path, "src/tests/integration/test_payment.py")
        self.assertEqual(ev.symbol, "TestPaymentFlow.test_process_charge")
        self.assertEqual(ev.start_line, 45)
        self.assertEqual(ev.end_line, 75)

    def test_handling_record_without_line_numbers(self) -> None:
        rec = self._make_record(start_line=None, end_line=None)
        result = self.builder.build([rec])
        ev = result.evidence[0]

        self.assertIsNone(ev.start_line)
        self.assertIsNone(ev.end_line)

    # -----------------------------------------------------------------------
    # 7. Commit SHA precedence
    # -----------------------------------------------------------------------

    def test_record_sha_precedence_over_fallback_sha(self) -> None:
        rec_with_sha = self._make_record(
            "t1",
            commit_sha="aaaa111122223333444455556666777788889999",
        )
        rec_without_sha = self._make_record(
            "t2",
            commit_sha=None,
        )

        fallback_sha = "bbbb111122223333444455556666777788889999"
        result = self.builder.build([rec_with_sha, rec_without_sha], commit_sha=fallback_sha)

        self.assertEqual(result.evidence[0].commit_sha, "aaaa111122223333444455556666777788889999")
        self.assertEqual(result.evidence[1].commit_sha, "bbbb111122223333444455556666777788889999")

    def test_fallback_sha_used_when_record_has_none(self) -> None:
        rec = self._make_record(commit_sha=None)
        result = self.builder.build([rec], commit_sha="cccc111122223333444455556666777788889999")
        self.assertEqual(result.evidence[0].commit_sha, "cccc111122223333444455556666777788889999")

    def test_no_sha_when_both_omitted(self) -> None:
        rec = self._make_record(commit_sha=None)
        result = self.builder.build([rec], commit_sha=None)
        self.assertIsNone(result.evidence[0].commit_sha)

    # -----------------------------------------------------------------------
    # 8. Empty input list
    # -----------------------------------------------------------------------

    def test_empty_input_list(self) -> None:
        result = self.builder.build([])
        self.assertIsInstance(result, TestEvidenceBuildResult)
        self.assertEqual(result.evidence, [])
        self.assertEqual(result.test_to_evidence, {})
        self.assertEqual(result.test_records, [])
        self.assertEqual(len(result), 0)

        evidence_list = self.builder.build_evidence([])
        self.assertEqual(evidence_list, [])

    # -----------------------------------------------------------------------
    # 9. Invalid inputs and failure behavior
    # -----------------------------------------------------------------------

    def test_non_list_input_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            self.builder.build("not_a_list")  # type: ignore[arg-type]
        self.assertIn("records must be a list", str(ctx.exception))

    def test_non_record_item_raises_type_error(self) -> None:
        valid_rec = self._make_record("t1")
        invalid_item = {"test_id": "t2"}
        with self.assertRaises(TypeError) as ctx:
            self.builder.build([valid_rec, invalid_item])  # type: ignore[list-item]
        self.assertIn("All items in records must be TestExecutionRecord instances", str(ctx.exception))

    def test_duplicate_test_id_in_records_raises_value_error(self) -> None:
        rec1 = self._make_record("duplicate_id", test_name="test_one")
        rec2 = self._make_record("duplicate_id", test_name="test_two")

        with self.assertRaises(ValueError) as ctx:
            self.builder.build([rec1, rec2])
        self.assertIn("Duplicate test_id 'duplicate_id' found in records", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 10. Convenience methods and aliases
    # -----------------------------------------------------------------------

    def test_build_evidence_returns_list_directly(self) -> None:
        rec = self._make_record()
        ev_list = self.builder.build_evidence([rec], repository="repo/name")

        self.assertIsInstance(ev_list, list)
        self.assertEqual(len(ev_list), 1)
        self.assertIsInstance(ev_list[0], Evidence)
        self.assertEqual(ev_list[0].evidence_id, "EV-1")

    def test_build_result_alias(self) -> None:
        rec = self._make_record()
        res = self.builder.build_result([rec])
        self.assertIsInstance(res, TestEvidenceBuildResult)
        self.assertEqual(len(res.evidence), 1)


if __name__ == "__main__":
    unittest.main()
