"""
DevSense — Unit Tests for Evidence Builder
==========================================
Tests the application-layer CodeEvidenceBuilder and CodeEvidenceBuildResult:
- One CodeObservation -> one Evidence
- Multiple CodeObservations -> multiple Evidence objects
- Deterministic sequential EV-1, EV-2 generation
- Observation-to-evidence traceability mapping
- Preservation of file_path, line range, symbol, description, repository, commit_sha
- Handling observations without line numbers or symbol
- Rejection of duplicate observation IDs
- Strict uniqueness of Evidence IDs
- No source code content stored in Evidence
- No Finding objects or verification statuses generated
- Deterministic repeated execution
- Handling of empty observation lists
"""

import unittest
from pydantic import ValidationError

from app.domain.models import Evidence, EvidenceType, Finding, FindingStatus
from app.investigation.schemas import (
    CodeEvidenceBuildResult,
    CodeObservation,
    CodeObservationType,
)
from app.investigation.service import CodeEvidenceBuilder, EvidenceBuilder


class TestCodeEvidenceBuilder(unittest.TestCase):
    """Unit tests for CodeEvidenceBuilder and CodeEvidenceBuildResult."""

    def setUp(self) -> None:
        self.builder = CodeEvidenceBuilder()

    def _make_observation(
        self,
        observation_id: str = "CODE-1",
        file_path: str = "app/services/payment.py",
        description: str = "Payment amount validation observed.",
        symbol: str | None = "validate_amount",
        start_line: int | None = 20,
        end_line: int | None = 35,
        observation_type: CodeObservationType = CodeObservationType.LOGIC,
        criterion_id: str | None = "AC-01",
    ) -> CodeObservation:
        return CodeObservation(
            observation_id=observation_id,
            file_path=file_path,
            description=description,
            symbol=symbol,
            start_line=start_line,
            end_line=end_line,
            observation_type=observation_type,
            criterion_id=criterion_id,
        )

    # -----------------------------------------------------------------------
    # 1. One CodeObservation -> one Evidence
    # -----------------------------------------------------------------------

    def test_one_observation_produces_one_evidence(self) -> None:
        obs = self._make_observation()
        result = self.builder.build([obs])

        self.assertIsInstance(result, CodeEvidenceBuildResult)
        self.assertEqual(len(result.evidence), 1)
        ev = result.evidence[0]
        self.assertIsInstance(ev, Evidence)
        self.assertEqual(ev.evidence_type, EvidenceType.CODE)
        self.assertEqual(ev.evidence_id, "EV-1")

    # -----------------------------------------------------------------------
    # 2. Multiple observations -> multiple Evidence objects
    # -----------------------------------------------------------------------

    def test_multiple_observations_produce_multiple_evidence(self) -> None:
        obs1 = self._make_observation("CODE-1", description="First observation")
        obs2 = self._make_observation("CODE-2", description="Second observation")
        obs3 = self._make_observation("CODE-3", description="Third observation")

        result = self.builder.build([obs1, obs2, obs3])

        self.assertEqual(len(result.evidence), 3)
        self.assertEqual([e.evidence_id for e in result.evidence], ["EV-1", "EV-2", "EV-3"])
        self.assertEqual([e.description for e in result.evidence], [
            "First observation",
            "Second observation",
            "Third observation",
        ])

    # -----------------------------------------------------------------------
    # 3. Deterministic EV-1, EV-2, etc.
    # -----------------------------------------------------------------------

    def test_deterministic_evidence_id_generation(self) -> None:
        obs_list = [
            self._make_observation(f"CODE-{i}", description=f"Obs {i}")
            for i in range(1, 6)
        ]
        result = self.builder.build(obs_list)

        expected_ids = ["EV-1", "EV-2", "EV-3", "EV-4", "EV-5"]
        actual_ids = [e.evidence_id for e in result.evidence]
        self.assertEqual(actual_ids, expected_ids)

    def test_custom_start_id(self) -> None:
        obs1 = self._make_observation("CODE-1")
        obs2 = self._make_observation("CODE-2")
        result = self.builder.build([obs1, obs2], start_id=10)

        self.assertEqual([e.evidence_id for e in result.evidence], ["EV-10", "EV-11"])

    def test_invalid_start_id_rejected(self) -> None:
        obs = self._make_observation("CODE-1")
        with self.assertRaises(ValueError):
            self.builder.build([obs], start_id=0)
        with self.assertRaises(ValueError):
            self.builder.build([obs], start_id=-5)

    # -----------------------------------------------------------------------
    # 4. Observation-to-evidence mapping
    # -----------------------------------------------------------------------

    def test_observation_to_evidence_mapping(self) -> None:
        obs1 = self._make_observation("CODE-10")
        obs2 = self._make_observation("CODE-20")

        result = self.builder.build([obs1, obs2])

        self.assertEqual(
            result.observation_to_evidence,
            {"CODE-10": "EV-1", "CODE-20": "EV-2"},
        )
        self.assertEqual(
            result.evidence_to_observation,
            {"EV-1": "CODE-10", "EV-2": "CODE-20"},
        )

    # -----------------------------------------------------------------------
    # 5. File path preservation
    # -----------------------------------------------------------------------

    def test_file_path_preservation(self) -> None:
        obs = self._make_observation(file_path="src/payments/processor.py")
        result = self.builder.build([obs])

        self.assertEqual(result.evidence[0].file_path, "src/payments/processor.py")

    # -----------------------------------------------------------------------
    # 6. Line range preservation
    # -----------------------------------------------------------------------

    def test_line_range_preservation(self) -> None:
        obs = self._make_observation(start_line=42, end_line=99)
        result = self.builder.build([obs])

        self.assertEqual(result.evidence[0].start_line, 42)
        self.assertEqual(result.evidence[0].end_line, 99)

    # -----------------------------------------------------------------------
    # 7. Symbol preservation
    # -----------------------------------------------------------------------

    def test_symbol_preservation(self) -> None:
        obs = self._make_observation(symbol="calculate_tax_rate")
        result = self.builder.build([obs])

        self.assertEqual(result.evidence[0].symbol, "calculate_tax_rate")

    # -----------------------------------------------------------------------
    # 8. Description preservation
    # -----------------------------------------------------------------------

    def test_description_preservation(self) -> None:
        exact_desc = "Validation logic checks idempotency key before database insertion."
        obs = self._make_observation(description=exact_desc)
        result = self.builder.build([obs])

        self.assertEqual(result.evidence[0].description, exact_desc)

    # -----------------------------------------------------------------------
    # 9. Repository preservation
    # -----------------------------------------------------------------------

    def test_repository_preservation(self) -> None:
        obs = self._make_observation()

        result_with_repo = self.builder.build([obs], repository="octocat/Hello-World")
        self.assertEqual(result_with_repo.evidence[0].repository, "octocat/Hello-World")

        result_none_repo = self.builder.build([obs], repository=None)
        self.assertIsNone(result_none_repo.evidence[0].repository)

        result_ws_repo = self.builder.build([obs], repository="   ")
        self.assertIsNone(result_ws_repo.evidence[0].repository)

    # -----------------------------------------------------------------------
    # 10. Commit SHA preservation
    # -----------------------------------------------------------------------

    def test_commit_sha_preservation(self) -> None:
        obs = self._make_observation()

        result_with_sha = self.builder.build([obs], commit_sha="7d4a2f8b")
        self.assertEqual(result_with_sha.evidence[0].commit_sha, "7d4a2f8b")

        result_none_sha = self.builder.build([obs], commit_sha=None)
        self.assertIsNone(result_none_sha.evidence[0].commit_sha)

        result_ws_sha = self.builder.build([obs], commit_sha="   ")
        self.assertIsNone(result_ws_sha.evidence[0].commit_sha)

    # -----------------------------------------------------------------------
    # 11. Observation without line numbers
    # -----------------------------------------------------------------------

    def test_observation_without_line_numbers(self) -> None:
        obs = self._make_observation(start_line=None, end_line=None)
        result = self.builder.build([obs])

        ev = result.evidence[0]
        self.assertIsNone(ev.start_line)
        self.assertIsNone(ev.end_line)
        self.assertEqual(ev.evidence_id, "EV-1")

    # -----------------------------------------------------------------------
    # 12. Observation without symbol
    # -----------------------------------------------------------------------

    def test_observation_without_symbol(self) -> None:
        obs = self._make_observation(symbol=None)
        result = self.builder.build([obs])

        ev = result.evidence[0]
        self.assertIsNone(ev.symbol)
        self.assertEqual(ev.evidence_id, "EV-1")

    # -----------------------------------------------------------------------
    # 13. Duplicate observation IDs rejected
    # -----------------------------------------------------------------------

    def test_duplicate_observation_ids_rejected(self) -> None:
        obs1 = self._make_observation("CODE-1", description="First")
        obs2 = self._make_observation("CODE-1", description="Duplicate ID")

        with self.assertRaises(ValueError) as ctx:
            self.builder.build([obs1, obs2])
        self.assertIn("Duplicate observation_id", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 14. Evidence IDs unique
    # -----------------------------------------------------------------------

    def test_evidence_ids_unique(self) -> None:
        obs_list = [
            self._make_observation(f"CODE-{i}", description=f"Obs {i}")
            for i in range(1, 15)
        ]
        result = self.builder.build(obs_list)

        ev_ids = [e.evidence_id for e in result.evidence]
        self.assertEqual(len(ev_ids), 14)
        self.assertEqual(len(set(ev_ids)), 14)

    # -----------------------------------------------------------------------
    # 15. No code content stored in Evidence
    # -----------------------------------------------------------------------

    def test_no_code_content_stored_in_evidence(self) -> None:
        obs = self._make_observation(
            description="Potential hard-coded credential pattern observed."
        )
        result = self.builder.build([obs])
        ev = result.evidence[0]

        # Evidence domain model must not have source content fields
        self.assertNotIn("content", ev.model_dump())
        self.assertNotIn("code", ev.model_dump())
        self.assertNotIn("source", ev.model_dump())
        self.assertNotIn("patch", ev.model_dump())
        # Description remains factual and concise, not whole file
        self.assertEqual(ev.description, "Potential hard-coded credential pattern observed.")

    # -----------------------------------------------------------------------
    # 16. No Finding objects generated
    # -----------------------------------------------------------------------

    def test_no_finding_objects_generated(self) -> None:
        obs = self._make_observation()
        result = self.builder.build([obs])

        for ev in result.evidence:
            self.assertNotIsInstance(ev, Finding)
            self.assertIsInstance(ev, Evidence)

    # -----------------------------------------------------------------------
    # 17. No verification status generated
    # -----------------------------------------------------------------------

    def test_no_verification_status_generated(self) -> None:
        obs = self._make_observation()
        result = self.builder.build([obs])

        dumped = result.model_dump()
        dumped_str = str(dumped)
        for forbidden in ["VERIFIED", "PARTIALLY_VERIFIED", "NOT_VERIFIED", "REQUIRES_REVIEW"]:
            self.assertNotIn(forbidden, dumped_str)

        for ev in result.evidence:
            self.assertFalse(hasattr(ev, "status"))
            self.assertFalse(hasattr(ev, "finding_status"))

    # -----------------------------------------------------------------------
    # 18. Deterministic repeated execution
    # -----------------------------------------------------------------------

    def test_deterministic_repeated_execution(self) -> None:
        obs1 = self._make_observation("CODE-1", description="Alpha")
        obs2 = self._make_observation("CODE-2", description="Beta")

        result1 = self.builder.build([obs1, obs2], repository="org/repo", commit_sha="abc1234")
        result2 = self.builder.build([obs1, obs2], repository="org/repo", commit_sha="abc1234")

        self.assertEqual(result1.model_dump(), result2.model_dump())

    # -----------------------------------------------------------------------
    # 19. Empty observation list handled cleanly
    # -----------------------------------------------------------------------

    def test_empty_observation_list_handled_cleanly(self) -> None:
        result = self.builder.build([])

        self.assertIsInstance(result, CodeEvidenceBuildResult)
        self.assertEqual(result.evidence, [])
        self.assertEqual(result.observation_to_evidence, {})
        self.assertEqual(len(result), 0)

    # -----------------------------------------------------------------------
    # Additional Robustness & Contract Tests
    # -----------------------------------------------------------------------

    def test_non_list_observations_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            self.builder.build("not-a-list")  # type: ignore[arg-type]

    def test_invalid_item_in_observations_raises_type_error(self) -> None:
        obs = self._make_observation("CODE-1")
        with self.assertRaises(TypeError):
            self.builder.build([obs, "invalid-item"])  # type: ignore[list-item]

    def test_build_evidence_convenience_method(self) -> None:
        obs1 = self._make_observation("CODE-1")
        obs2 = self._make_observation("CODE-2")
        evidence_list = self.builder.build_evidence([obs1, obs2])

        self.assertIsInstance(evidence_list, list)
        self.assertEqual(len(evidence_list), 2)
        self.assertIsInstance(evidence_list[0], Evidence)

    def test_evidence_builder_alias(self) -> None:
        self.assertIs(EvidenceBuilder, CodeEvidenceBuilder)
        alias_builder = EvidenceBuilder()
        obs = self._make_observation("CODE-1")
        result = alias_builder.build([obs])
        self.assertEqual(len(result.evidence), 1)

    def test_result_dto_iteration_and_indexing(self) -> None:
        obs1 = self._make_observation("CODE-1", description="First")
        obs2 = self._make_observation("CODE-2", description="Second")
        result = self.builder.build([obs1, obs2])

        # Iteration
        evidence_items = list(result)
        self.assertEqual(len(evidence_items), 2)
        self.assertEqual(evidence_items[0].evidence_id, "EV-1")

        # Len
        self.assertEqual(len(result), 2)

        # Indexing
        self.assertEqual(result[0].evidence_id, "EV-1")
        self.assertEqual(result[1].evidence_id, "EV-2")

    def test_result_dto_validation_duplicate_evidence_id_rejected(self) -> None:
        ev1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            description="Sample 1",
        )
        ev2 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            description="Sample 2",
        )
        with self.assertRaises(ValueError):
            CodeEvidenceBuildResult(
                evidence=[ev1, ev2],
                observation_to_evidence={"CODE-1": "EV-1", "CODE-2": "EV-1"},
            )

    def test_result_dto_validation_traceability_mismatch_rejected(self) -> None:
        ev1 = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            description="Sample 1",
        )
        # Mismatched evidence ID in mapping
        with self.assertRaises(ValueError):
            CodeEvidenceBuildResult(
                evidence=[ev1],
                observation_to_evidence={"CODE-1": "EV-999"},
            )

        # Mismatched length
        with self.assertRaises(ValueError):
            CodeEvidenceBuildResult(
                evidence=[ev1],
                observation_to_evidence={"CODE-1": "EV-1", "CODE-2": "EV-2"},
            )


if __name__ == "__main__":
    unittest.main()
