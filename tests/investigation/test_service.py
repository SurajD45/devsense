"""
Tests for RequirementAnalysisService
=====================================
Validates the deterministic transformation of domain Requirement
objects into RequirementAnalysis DTOs.
"""

import unittest

from app.domain.models import AcceptanceCriterion, Requirement
from app.investigation.schemas import AnalyzedCriterion, RequirementAnalysis
from app.investigation.service import RequirementAnalysisService


class TestRequirementAnalysisService(unittest.TestCase):
    """Tests for RequirementAnalysisService.analyze()."""

    def setUp(self):
        self.service = RequirementAnalysisService()

    # -- helpers --

    def _make_criterion(self, cid="AC-1", title="Criterion title", description=None):
        return AcceptanceCriterion(
            criterion_id=cid,
            title=title,
            description=description,
        )

    def _make_requirement(self, **overrides):
        defaults = dict(
            requirement_id="REQ-1",
            title="Add login feature",
            description="Users must be able to log in with email and password.",
            status="Active",
            acceptance_criteria=[
                self._make_criterion("AC-1", "User can log in with valid credentials"),
            ],
        )
        defaults.update(overrides)
        return Requirement(**defaults)

    # -- return type --

    def test_returned_object_is_requirement_analysis(self):
        result = self.service.analyze(self._make_requirement())
        self.assertIsInstance(result, RequirementAnalysis)

    # -- requirement_id preserved --

    def test_requirement_id_preserved(self):
        result = self.service.analyze(self._make_requirement(requirement_id="PAY-142"))
        self.assertEqual(result.requirement_id, "PAY-142")

    # -- summary generation --

    def test_summary_includes_title(self):
        result = self.service.analyze(self._make_requirement(title="Add payment retry"))
        self.assertIn("Add payment retry", result.summary)

    def test_summary_includes_description_when_present(self):
        result = self.service.analyze(
            self._make_requirement(
                title="Add payment retry",
                description="Retry failed payments with exponential backoff.",
            )
        )
        self.assertIn("Add payment retry", result.summary)
        self.assertIn("Retry failed payments with exponential backoff.", result.summary)

    def test_summary_without_description(self):
        result = self.service.analyze(
            self._make_requirement(title="Add login feature", description=None)
        )
        self.assertIn("Add login feature", result.summary)
        # Should still produce a non-empty summary
        self.assertTrue(len(result.summary) > 0)

    # -- single acceptance criterion --

    def test_single_criterion(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "User can log in with valid credentials"),
            ],
        )
        result = self.service.analyze(req)
        self.assertEqual(len(result.criteria), 1)
        ac = result.criteria[0]
        self.assertIsInstance(ac, AnalyzedCriterion)
        self.assertEqual(ac.criterion_id, "AC-1")

    # -- multiple acceptance criteria --

    def test_multiple_criteria(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "User can log in"),
                self._make_criterion("AC-2", "User can log out"),
                self._make_criterion("AC-3", "Session expires after 30 minutes"),
            ],
        )
        result = self.service.analyze(req)
        self.assertEqual(len(result.criteria), 3)
        ids = [c.criterion_id for c in result.criteria]
        self.assertEqual(ids, ["AC-1", "AC-2", "AC-3"])

    # -- criterion IDs preserved --

    def test_criterion_ids_preserved(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-42", "High ID criterion"),
                self._make_criterion("AC-99", "Another criterion"),
            ],
        )
        result = self.service.analyze(req)
        ids = [c.criterion_id for c in result.criteria]
        self.assertEqual(ids, ["AC-42", "AC-99"])

    # -- interpretation generation --

    def test_interpretation_contains_criterion_title(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "Retry failed payments up to 3 times"),
            ],
        )
        result = self.service.analyze(req)
        self.assertIn(
            "Retry failed payments up to 3 times",
            result.criteria[0].interpretation,
        )

    def test_interpretation_includes_description_when_present(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion(
                    "AC-1",
                    "Retry failed payments",
                    description="Use exponential backoff with a maximum of 3 attempts.",
                ),
            ],
        )
        result = self.service.analyze(req)
        interpretation = result.criteria[0].interpretation
        self.assertIn("Retry failed payments", interpretation)
        self.assertIn("exponential backoff", interpretation)

    def test_interpretation_without_criterion_description(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "User receives a confirmation email"),
            ],
        )
        result = self.service.analyze(req)
        self.assertIn(
            "User receives a confirmation email",
            result.criteria[0].interpretation,
        )

    # -- verification point generation --

    def test_at_least_one_verification_point_per_criterion(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "Login works"),
                self._make_criterion("AC-2", "Logout works"),
            ],
        )
        result = self.service.analyze(req)
        for ac in result.criteria:
            self.assertGreaterEqual(len(ac.verification_points), 1)

    def test_verification_points_reference_criterion_text(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "Retry failed payments up to 3 times"),
            ],
        )
        result = self.service.analyze(req)
        points_joined = " ".join(result.criteria[0].verification_points)
        self.assertIn("Retry failed payments up to 3 times", points_joined)

    def test_verification_points_include_description_when_present(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion(
                    "AC-1",
                    "Retry failed payments",
                    description="Maximum of 3 attempts with backoff.",
                ),
            ],
        )
        result = self.service.analyze(req)
        points = result.criteria[0].verification_points
        self.assertGreaterEqual(len(points), 2)
        points_joined = " ".join(points)
        self.assertIn("Maximum of 3 attempts with backoff.", points_joined)

    def test_verification_points_are_strings(self):
        result = self.service.analyze(self._make_requirement())
        for ac in result.criteria:
            for point in ac.verification_points:
                self.assertIsInstance(point, str)

    # -- potential_areas remain empty --

    def test_potential_areas_empty_for_all_criteria(self):
        req = self._make_requirement(
            acceptance_criteria=[
                self._make_criterion("AC-1", "Login works"),
                self._make_criterion("AC-2", "Logout works"),
            ],
        )
        result = self.service.analyze(req)
        for ac in result.criteria:
            self.assertEqual(ac.potential_areas, [])

    # -- empty acceptance criteria rejected --

    def test_empty_acceptance_criteria_raises_value_error(self):
        req = self._make_requirement(acceptance_criteria=[])
        with self.assertRaises(ValueError) as ctx:
            self.service.analyze(req)
        self.assertIn("no acceptance criteria", str(ctx.exception))

    def test_error_message_includes_requirement_id(self):
        req = self._make_requirement(requirement_id="PAY-99", acceptance_criteria=[])
        with self.assertRaises(ValueError) as ctx:
            self.service.analyze(req)
        self.assertIn("PAY-99", str(ctx.exception))

    # -- no Finding objects created --

    def test_no_finding_objects_in_result(self):
        """The analysis stage must not produce Finding objects."""
        result = self.service.analyze(self._make_requirement())
        data = result.model_dump()
        # RequirementAnalysis has no finding-related fields
        self.assertNotIn("findings", data)
        self.assertNotIn("finding_ids", data)

    # -- service requires no external dependencies --

    def test_service_requires_no_constructor_args(self):
        """Service must be instantiable with no dependencies."""
        svc = RequirementAnalysisService()
        self.assertIsNotNone(svc)

    def test_service_has_no_external_attributes(self):
        """Service must not hold references to external clients."""
        svc = RequirementAnalysisService()
        attrs = vars(svc)
        # A pure service should have no instance state
        self.assertEqual(len(attrs), 0)

    # -- full round-trip serialization --

    def test_full_round_trip_serialization(self):
        req = self._make_requirement(
            requirement_id="PAY-142",
            title="Add payment retry",
            description="Retry failed payments with exponential backoff.",
            acceptance_criteria=[
                self._make_criterion(
                    "AC-1",
                    "Retry failed payments up to 3 times",
                    description="Each retry uses exponential backoff.",
                ),
                self._make_criterion(
                    "AC-2",
                    "Log each retry attempt",
                ),
            ],
        )
        result = self.service.analyze(req)
        data = result.model_dump()

        self.assertEqual(data["requirement_id"], "PAY-142")
        self.assertIn("Add payment retry", data["summary"])
        self.assertEqual(len(data["criteria"]), 2)
        self.assertEqual(data["criteria"][0]["criterion_id"], "AC-1")
        self.assertEqual(data["criteria"][1]["criterion_id"], "AC-2")
        self.assertEqual(data["criteria"][0]["potential_areas"], [])
        self.assertEqual(data["criteria"][1]["potential_areas"], [])


if __name__ == "__main__":
    unittest.main()
