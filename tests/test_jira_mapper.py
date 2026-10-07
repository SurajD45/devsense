"""
DevSense — Unit Tests for JiraRequirementMapper
================================================
Tests the mapping from normalized JiraIssue objects to canonical
DevSense Requirement domain models.
"""

import unittest

from app.domain.models import AcceptanceCriterion, Requirement
from app.integrations.jira.mapper import JiraRequirementMapper
from app.integrations.jira.models import JiraIssue


class TestJiraRequirementMapper(unittest.TestCase):
    """Unit tests for JiraRequirementMapper."""

    def setUp(self) -> None:
        self.mapper = JiraRequirementMapper()
        self.base_url = "https://example.atlassian.net"

    def _make_jira_issue(self, **overrides) -> JiraIssue:
        defaults = dict(
            key="ABC-123",
            summary="Add coupon validation",
            description="Validate coupon code and expiration date before checkout.",
            status="In Progress",
            issue_type="Story",
            priority="High",
            acceptance_criteria=[
                "AC-1: Coupon code must be checked against database",
                "AC-2: Expired coupons must return error",
            ],
        )
        defaults.update(overrides)
        return JiraIssue(**defaults)

    # -----------------------------------------------------------------------
    # 1. Complete mapping
    # -----------------------------------------------------------------------

    def test_complete_mapping(self) -> None:
        issue = self._make_jira_issue()
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertIsInstance(requirement, Requirement)
        self.assertEqual(requirement.requirement_id, "ABC-123")
        self.assertEqual(requirement.title, "Add coupon validation")
        self.assertEqual(
            requirement.description,
            "Validate coupon code and expiration date before checkout.",
        )
        self.assertEqual(requirement.status, "In Progress")
        self.assertEqual(requirement.priority, "High")
        self.assertEqual(
            requirement.source,
            "https://example.atlassian.net/browse/ABC-123",
        )
        self.assertEqual(len(requirement.acceptance_criteria), 2)

        ac1 = requirement.acceptance_criteria[0]
        self.assertIsInstance(ac1, AcceptanceCriterion)
        self.assertEqual(ac1.criterion_id, "AC-1")
        self.assertEqual(ac1.title, "Coupon code must be checked against database")
        self.assertIsNone(ac1.description)

        ac2 = requirement.acceptance_criteria[1]
        self.assertIsInstance(ac2, AcceptanceCriterion)
        self.assertEqual(ac2.criterion_id, "AC-2")
        self.assertEqual(ac2.title, "Expired coupons must return error")
        self.assertIsNone(ac2.description)

    # -----------------------------------------------------------------------
    # 2. Source URL
    # -----------------------------------------------------------------------

    def test_source_url_construction(self) -> None:
        issue = self._make_jira_issue(key="PAY-42")
        requirement = self.mapper.to_requirement(issue, "https://company.atlassian.net")

        self.assertEqual(requirement.source, "https://company.atlassian.net/browse/PAY-42")

    # -----------------------------------------------------------------------
    # 3. Explicit AC identifiers
    # -----------------------------------------------------------------------

    def test_explicit_ac_identifiers_preserved(self) -> None:
        issue = self._make_jira_issue(
            acceptance_criteria=[
                "AC-7: Validate card",
                "AC-9: Validate expiry",
            ]
        )
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertEqual(len(requirement.acceptance_criteria), 2)
        self.assertEqual(requirement.acceptance_criteria[0].criterion_id, "AC-7")
        self.assertEqual(requirement.acceptance_criteria[0].title, "Validate card")
        self.assertEqual(requirement.acceptance_criteria[1].criterion_id, "AC-9")
        self.assertEqual(requirement.acceptance_criteria[1].title, "Validate expiry")

    # -----------------------------------------------------------------------
    # 4. Criteria without identifiers
    # -----------------------------------------------------------------------

    def test_criteria_without_identifiers_generate_sequential_ids(self) -> None:
        issue = self._make_jira_issue(
            acceptance_criteria=[
                "Validate card",
                "Validate expiry",
            ]
        )
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertEqual(len(requirement.acceptance_criteria), 2)
        self.assertEqual(requirement.acceptance_criteria[0].criterion_id, "AC-1")
        self.assertEqual(requirement.acceptance_criteria[0].title, "Validate card")
        self.assertEqual(requirement.acceptance_criteria[1].criterion_id, "AC-2")
        self.assertEqual(requirement.acceptance_criteria[1].title, "Validate expiry")

    # -----------------------------------------------------------------------
    # 5. Mixed criteria
    # -----------------------------------------------------------------------

    def test_mixed_criteria_deterministic_id_generation(self) -> None:
        """
        Deterministic behavior for mixed criteria:
        - Explicit IDs (AC-4, AC-9) are preserved.
        - Non-explicit criteria receive sequential IDs starting at AC-1 that do not
          collide with explicitly declared IDs.
        Input: ["AC-4: Validate card", "Validate expiry", "AC-9: Validate CVV"]
        Expected: AC-4, AC-1, AC-9
        """
        issue = self._make_jira_issue(
            acceptance_criteria=[
                "AC-4: Validate card",
                "Validate expiry",
                "AC-9: Validate CVV",
            ]
        )
        requirement = self.mapper.to_requirement(issue, self.base_url)

        ids = [ac.criterion_id for ac in requirement.acceptance_criteria]
        self.assertEqual(ids, ["AC-4", "AC-1", "AC-9"])
        self.assertEqual(requirement.acceptance_criteria[0].title, "Validate card")
        self.assertEqual(requirement.acceptance_criteria[1].title, "Validate expiry")
        self.assertEqual(requirement.acceptance_criteria[2].title, "Validate CVV")

    # -----------------------------------------------------------------------
    # 6. Empty acceptance criteria
    # -----------------------------------------------------------------------

    def test_empty_acceptance_criteria_produces_empty_list(self) -> None:
        issue = self._make_jira_issue(acceptance_criteria=[])
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertEqual(requirement.acceptance_criteria, [])

    # -----------------------------------------------------------------------
    # 7. Missing Jira status
    # -----------------------------------------------------------------------

    def test_missing_jira_status_raises_value_error(self) -> None:
        issue_none = self._make_jira_issue(status=None)
        with self.assertRaises(ValueError) as ctx_none:
            self.mapper.to_requirement(issue_none, self.base_url)
        self.assertIn("status is required", str(ctx_none.exception))

        issue_empty = self._make_jira_issue(status="   ")
        with self.assertRaises(ValueError) as ctx_empty:
            self.mapper.to_requirement(issue_empty, self.base_url)
        self.assertIn("status is required", str(ctx_empty.exception))

    # -----------------------------------------------------------------------
    # 8. Missing description
    # -----------------------------------------------------------------------

    def test_missing_description_preserved_as_none(self) -> None:
        issue = self._make_jira_issue(description=None)
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertIsNone(requirement.description)

    # -----------------------------------------------------------------------
    # 9. Missing priority
    # -----------------------------------------------------------------------

    def test_missing_priority_preserved_as_none(self) -> None:
        issue = self._make_jira_issue(priority=None)
        requirement = self.mapper.to_requirement(issue, self.base_url)

        self.assertIsNone(requirement.priority)

    # -----------------------------------------------------------------------
    # 10. Base URL trailing slash normalization
    # -----------------------------------------------------------------------

    def test_base_url_trailing_slash_normalized(self) -> None:
        issue = self._make_jira_issue(key="ABC-123")
        requirement = self.mapper.to_requirement(issue, "https://example.atlassian.net/")

        self.assertEqual(requirement.source, "https://example.atlassian.net/browse/ABC-123")

    # -----------------------------------------------------------------------
    # 11. Additional validation & robustness tests
    # -----------------------------------------------------------------------

    def test_invalid_base_url_raises_value_error(self) -> None:
        issue = self._make_jira_issue()
        with self.assertRaises(ValueError):
            self.mapper.to_requirement(issue, "")
        with self.assertRaises(ValueError):
            self.mapper.to_requirement(issue, "   ")

    def test_public_export_from_jira_package(self) -> None:
        from app.integrations.jira import JiraClient, JiraRequirementMapper

        self.assertIsNotNone(JiraClient)
        self.assertIsNotNone(JiraRequirementMapper)


if __name__ == "__main__":
    unittest.main()
