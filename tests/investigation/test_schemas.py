"""
Tests for DevSense Investigation Schemas (DTOs)
================================================
Focuses on RequirementAnalysis and AnalyzedCriterion contracts.
"""

import unittest
from pydantic import ValidationError

from app.investigation import AnalyzedCriterion, RequirementAnalysis


class TestAnalyzedCriterionSchema(unittest.TestCase):
    """Tests for the AnalyzedCriterion DTO."""

    def _make_criterion(self, **overrides):
        defaults = dict(
            criterion_id="AC-1",
            interpretation="Failed payment requests should be retried, with no more than three attempts.",
            verification_points=[
                "retry occurs after payment failure",
                "maximum retry count is three",
                "retry count is not reset incorrectly",
            ],
            potential_areas=[
                "payment service",
                "retry handling",
                "payment state",
            ],
        )
        defaults.update(overrides)
        return defaults

    # -- valid construction --

    def test_valid_criterion_full(self):
        c = AnalyzedCriterion(**self._make_criterion())
        self.assertEqual(c.criterion_id, "AC-1")
        self.assertEqual(
            c.interpretation,
            "Failed payment requests should be retried, with no more than three attempts.",
        )
        self.assertEqual(len(c.verification_points), 3)
        self.assertEqual(len(c.potential_areas), 3)

    def test_valid_criterion_default_potential_areas_is_empty_list(self):
        kwargs = self._make_criterion()
        del kwargs["potential_areas"]
        c = AnalyzedCriterion(**kwargs)
        self.assertEqual(c.potential_areas, [])

    def test_valid_criterion_with_dots_in_conceptual_names(self):
        """Legitimate conceptual names with dots must NOT be rejected."""
        valid_conceptual_names = [
            "auth.service",
            "payment.retry.manager",
            "api.v2",
            "domain.core",
            "v1.0",
        ]
        c = AnalyzedCriterion(
            **self._make_criterion(potential_areas=valid_conceptual_names)
        )
        self.assertEqual(c.potential_areas, valid_conceptual_names)

    # -- criterion_id validation --

    def test_valid_criterion_id_patterns(self):
        for cid in ["AC-0", "AC-1", "AC-42", "AC-999"]:
            c = AnalyzedCriterion(**self._make_criterion(criterion_id=cid))
            self.assertEqual(c.criterion_id, cid)

    def test_invalid_criterion_id_patterns(self):
        invalid_ids = ["ac-1", "AC1", "AC-", "AC-abc", "CRIT-1", "AC -1"]
        for cid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Failed for id: {cid}"):
                AnalyzedCriterion(**self._make_criterion(criterion_id=cid))

    def test_criterion_id_whitespace_stripped(self):
        c = AnalyzedCriterion(**self._make_criterion(criterion_id="  AC-7  "))
        self.assertEqual(c.criterion_id, "AC-7")

    def test_missing_criterion_id_rejected(self):
        kwargs = self._make_criterion()
        del kwargs["criterion_id"]
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(**kwargs)

    # -- interpretation validation --

    def test_interpretation_whitespace_stripped(self):
        c = AnalyzedCriterion(**self._make_criterion(interpretation="  Meaning  "))
        self.assertEqual(c.interpretation, "Meaning")

    def test_empty_interpretation_rejected(self):
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(**self._make_criterion(interpretation="   "))

    def test_missing_interpretation_rejected(self):
        kwargs = self._make_criterion()
        del kwargs["interpretation"]
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(**kwargs)

    # -- verification_points validation --

    def test_empty_verification_points_list_rejected(self):
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(**self._make_criterion(verification_points=[]))

    def test_verification_points_whitespace_stripped(self):
        c = AnalyzedCriterion(
            **self._make_criterion(verification_points=["  point 1  ", "  point 2  "])
        )
        self.assertEqual(c.verification_points, ["point 1", "point 2"])

    def test_verification_point_whitespace_only_rejected(self):
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(
                **self._make_criterion(verification_points=["valid", "   "])
            )

    # -- potential_areas validation (anti-file-path invariant) --

    def test_potential_areas_rejects_forward_slash_path(self):
        invalid_areas = [
            "src/payment",
            "app/integrations/github/service.py",
            "./services",
            "services/retry",
        ]
        for area in invalid_areas:
            with self.assertRaises(ValidationError, msg=f"Failed to reject: {area}"):
                AnalyzedCriterion(**self._make_criterion(potential_areas=[area]))

    def test_potential_areas_rejects_backslash_path(self):
        invalid_areas = [
            "app\\auth",
            "src\\services\\payment.py",
            ".\\controllers",
        ]
        for area in invalid_areas:
            with self.assertRaises(ValidationError, msg=f"Failed to reject: {area}"):
                AnalyzedCriterion(**self._make_criterion(potential_areas=[area]))

    def test_potential_areas_rejects_specific_code_files(self):
        file_names = [
            "auth.py",
            "payment.ts",
            "service.js",
            "component.tsx",
            "handler.go",
            "Order.java",
            "config.json",
            "pipeline.yaml",
            "workflow.yml",
        ]
        for fname in file_names:
            with self.assertRaises(ValidationError, msg=f"Failed to reject: {fname}"):
                AnalyzedCriterion(**self._make_criterion(potential_areas=[fname]))

    def test_potential_areas_rejects_whitespace_only_string(self):
        with self.assertRaises(ValidationError):
            AnalyzedCriterion(**self._make_criterion(potential_areas=["   "]))

    def test_potential_areas_whitespace_stripped(self):
        c = AnalyzedCriterion(
            **self._make_criterion(potential_areas=["  payment service  "])
        )
        self.assertEqual(c.potential_areas, ["payment service"])

    # -- serialization --

    def test_model_dump(self):
        c = AnalyzedCriterion(**self._make_criterion())
        data = c.model_dump()
        self.assertEqual(data["criterion_id"], "AC-1")
        self.assertEqual(len(data["verification_points"]), 3)
        self.assertEqual(len(data["potential_areas"]), 3)


class TestRequirementAnalysisSchema(unittest.TestCase):
    """Tests for the RequirementAnalysis DTO."""

    def _make_criterion(self, cid="AC-1"):
        return AnalyzedCriterion(
            criterion_id=cid,
            interpretation="Operational meaning.",
            verification_points=["condition 1", "condition 2"],
            potential_areas=["payment service"],
        )

    def _make_analysis(self, **overrides):
        defaults = dict(
            requirement_id="PAY-142",
            summary="Add payment retry functionality with exponential backoff.",
            criteria=[self._make_criterion("AC-1"), self._make_criterion("AC-2")],
        )
        defaults.update(overrides)
        return defaults

    # -- valid construction --

    def test_valid_analysis_full(self):
        ra = RequirementAnalysis(**self._make_analysis())
        self.assertEqual(ra.requirement_id, "PAY-142")
        self.assertEqual(
            ra.summary,
            "Add payment retry functionality with exponential backoff.",
        )
        self.assertEqual(len(ra.criteria), 2)

    def test_valid_analysis_default_empty_criteria(self):
        kwargs = self._make_analysis()
        del kwargs["criteria"]
        ra = RequirementAnalysis(**kwargs)
        self.assertEqual(ra.criteria, [])

    # -- requirement_id validation --

    def test_requirement_id_whitespace_stripped(self):
        ra = RequirementAnalysis(**self._make_analysis(requirement_id="  REQ-99  "))
        self.assertEqual(ra.requirement_id, "REQ-99")

    def test_empty_requirement_id_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementAnalysis(**self._make_analysis(requirement_id="   "))

    def test_missing_requirement_id_rejected(self):
        kwargs = self._make_analysis()
        del kwargs["requirement_id"]
        with self.assertRaises(ValidationError):
            RequirementAnalysis(**kwargs)

    # -- summary validation --

    def test_summary_whitespace_stripped(self):
        ra = RequirementAnalysis(**self._make_analysis(summary="  summary text  "))
        self.assertEqual(ra.summary, "summary text")

    def test_empty_summary_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementAnalysis(**self._make_analysis(summary="   "))

    def test_missing_summary_rejected(self):
        kwargs = self._make_analysis()
        del kwargs["summary"]
        with self.assertRaises(ValidationError):
            RequirementAnalysis(**kwargs)

    # -- criteria validation --

    def test_duplicate_criterion_id_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementAnalysis(
                **self._make_analysis(
                    criteria=[
                        self._make_criterion("AC-1"),
                        self._make_criterion("AC-1"),
                    ]
                )
            )

    # -- serialization --

    def test_model_dump_matches_expected_contract(self):
        ra = RequirementAnalysis(
            requirement_id="PAY-142",
            summary="Add payment retry",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Failed payment requests should be retried, with no more than three attempts.",
                    verification_points=[
                        "retry occurs after payment failure",
                        "maximum retry count is three",
                        "retry count is not reset incorrectly",
                    ],
                    potential_areas=[
                        "payment service",
                        "retry handling",
                        "payment state",
                    ],
                )
            ],
        )
        data = ra.model_dump()
        self.assertEqual(data["requirement_id"], "PAY-142")
        self.assertEqual(data["summary"], "Add payment retry")
        self.assertEqual(len(data["criteria"]), 1)
        ac_data = data["criteria"][0]
        self.assertEqual(ac_data["criterion_id"], "AC-1")
        self.assertEqual(len(ac_data["verification_points"]), 3)
        self.assertEqual(
            ac_data["potential_areas"],
            ["payment service", "retry handling", "payment state"],
        )


if __name__ == "__main__":
    unittest.main()
