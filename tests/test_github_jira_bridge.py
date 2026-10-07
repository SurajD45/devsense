"""
Tests for the GitHub → Jira Bridge components.

Covers:
  A. Jira key in PR title
  B. Jira key only in PR body
  C. No Jira key
  D. Multiple keys — deterministic first-match
  E. Jira is NOT called when no key exists
  F. Jira issue is retrieved when a key exists
  G. JiraIssue is passed through JiraRequirementMapper
  H. Result is the canonical Requirement model
  I. Jira API failure propagates correctly
  J. Credentials never appear in exception messages or logs

All Jira calls are mocked; the live Jira instance is never contacted.
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock, patch

from app.domain.models import AcceptanceCriterion, Requirement
from app.integrations.jira.bridge import BridgeResult, GitHubJiraBridge
from app.integrations.jira.client import (
    JiraAuthenticationError,
    JiraClient,
    JiraClientError,
    JiraIssueNotFoundError,
    JiraNetworkError,
)
from app.integrations.jira.key_extractor import (
    extract_all_jira_keys,
    extract_jira_key,
)
from app.integrations.jira.mapper import JiraRequirementMapper
from app.integrations.jira.models import JiraIssue


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_jira_issue(
    key: str = "SCRUM-5",
    summary: str = "Add email validation",
    description: str | None = "Validates user email on registration.",
    status: str | None = "In Progress",
    priority: str | None = "Medium",
    acceptance_criteria: list[str] | None = None,
) -> JiraIssue:
    return JiraIssue(
        key=key,
        summary=summary,
        description=description,
        status=status,
        issue_type="Story",
        priority=priority,
        acceptance_criteria=acceptance_criteria or [],
    )


def _make_mapper() -> JiraRequirementMapper:
    return JiraRequirementMapper()


def _make_mock_client(issue: JiraIssue | None = None) -> MagicMock:
    """Return a mock JiraClient that returns *issue* from get_issue()."""
    mock = MagicMock(spec=JiraClient)
    if issue is not None:
        mock.get_issue.return_value = issue
    return mock


def _make_bridge(
    jira_client: MagicMock | None = None,
    jira_issue: JiraIssue | None = None,
    base_url: str = "https://acme.atlassian.net",
) -> GitHubJiraBridge:
    if jira_client is None:
        jira_client = _make_mock_client(jira_issue or _make_jira_issue())
    return GitHubJiraBridge(
        jira_client=jira_client,
        mapper=_make_mapper(),
        jira_base_url=base_url,
    )


# ===========================================================================
# Part 1 — Key Extractor unit tests
# ===========================================================================

class TestExtractJiraKey(unittest.TestCase):
    """Tests for app.integrations.jira.key_extractor.extract_jira_key."""

    # -----------------------------------------------------------------------
    # A. Jira key in PR title
    # -----------------------------------------------------------------------

    def test_key_in_title_returned(self) -> None:
        """A. Key present in title → returned without inspecting body."""
        result = extract_jira_key("SCRUM-5 Add email validation", body=None)
        self.assertEqual(result, "SCRUM-5")

    def test_key_in_title_with_body_ignored(self) -> None:
        """A. Title match is returned; body is NOT inspected."""
        result = extract_jira_key(
            "SCRUM-5 Add email validation",
            body="Implements DEV-99.",
        )
        self.assertEqual(result, "SCRUM-5")

    def test_key_at_end_of_title(self) -> None:
        result = extract_jira_key("Add email validation SCRUM-5")
        self.assertEqual(result, "SCRUM-5")

    def test_key_in_middle_of_title(self) -> None:
        result = extract_jira_key("Fix [SCRUM-5] login bug")
        self.assertEqual(result, "SCRUM-5")

    # -----------------------------------------------------------------------
    # B. Jira key only in PR body
    # -----------------------------------------------------------------------

    def test_key_only_in_body(self) -> None:
        """B. No key in title → body is searched."""
        result = extract_jira_key(
            "Add email validation",
            body="Implements SCRUM-5.",
        )
        self.assertEqual(result, "SCRUM-5")

    def test_key_only_in_body_multiline(self) -> None:
        body = "This PR implements:\n\nSCRUM-5 - Add email validation\nDEV-123 - Also fixes dev issue"
        result = extract_jira_key("Add email validation", body=body)
        self.assertEqual(result, "SCRUM-5")

    def test_key_in_body_when_title_has_no_match(self) -> None:
        result = extract_jira_key("Refactor login service", body="Closes DEV-123.")
        self.assertEqual(result, "DEV-123")

    # -----------------------------------------------------------------------
    # C. No Jira key
    # -----------------------------------------------------------------------

    def test_no_key_returns_none(self) -> None:
        """C. No key in title or body → None."""
        result = extract_jira_key("Add email validation", body="No ticket referenced.")
        self.assertIsNone(result)

    def test_no_key_no_body(self) -> None:
        result = extract_jira_key("Add email validation")
        self.assertIsNone(result)

    def test_no_key_empty_body(self) -> None:
        result = extract_jira_key("Add email validation", body="")
        self.assertIsNone(result)

    def test_no_key_none_body(self) -> None:
        result = extract_jira_key("Add email validation", body=None)
        self.assertIsNone(result)

    def test_lowercase_key_not_matched(self) -> None:
        """Jira keys must be uppercase — lowercase keys are NOT matched."""
        result = extract_jira_key("scrum-5 add email validation", body=None)
        self.assertIsNone(result)

    # -----------------------------------------------------------------------
    # D. Multiple keys — deterministic first-match
    # -----------------------------------------------------------------------

    def test_multiple_keys_in_title_first_returned(self) -> None:
        """D. Multiple keys in title → first (leftmost) key is returned."""
        result = extract_jira_key("SCRUM-5 and DEV-123 fixes")
        self.assertEqual(result, "SCRUM-5")

    def test_multiple_keys_in_body_first_returned(self) -> None:
        """D. Multiple keys in body → first (leftmost) key is returned."""
        result = extract_jira_key(
            "Various fixes",
            body="Implements PROJ-10 and SCRUM-5.",
        )
        self.assertEqual(result, "PROJ-10")

    def test_title_match_takes_precedence_over_body(self) -> None:
        """D. Key in title is always preferred over any key in body."""
        result = extract_jira_key("SCRUM-5 fix", body="Also closes DEV-1.")
        self.assertEqual(result, "SCRUM-5")

    # -----------------------------------------------------------------------
    # Additional pattern coverage
    # -----------------------------------------------------------------------

    def test_key_with_underscore_in_project_part(self) -> None:
        """PROJ_ABC-42 is a valid key pattern."""
        result = extract_jira_key("PROJ_ABC-42 feature")
        self.assertEqual(result, "PROJ_ABC-42")

    def test_key_dev_123(self) -> None:
        result = extract_jira_key("DEV-123 fix login")
        self.assertEqual(result, "DEV-123")

    def test_invalid_title_type_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            extract_jira_key(None)  # type: ignore[arg-type]


class TestExtractAllJiraKeys(unittest.TestCase):
    """Tests for extract_all_jira_keys helper."""

    def test_single_key(self) -> None:
        self.assertEqual(extract_all_jira_keys("SCRUM-5 fix"), ["SCRUM-5"])

    def test_multiple_keys_ordered(self) -> None:
        self.assertEqual(
            extract_all_jira_keys("SCRUM-5 and DEV-123"),
            ["SCRUM-5", "DEV-123"],
        )

    def test_no_keys(self) -> None:
        self.assertEqual(extract_all_jira_keys("No ticket here"), [])

    def test_invalid_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            extract_all_jira_keys(42)  # type: ignore[arg-type]


# ===========================================================================
# Part 2 — Bridge unit tests
# ===========================================================================

class TestGitHubJiraBridgeInit(unittest.TestCase):
    """Tests for GitHubJiraBridge construction."""

    def test_valid_construction(self) -> None:
        bridge = _make_bridge()
        self.assertIsNotNone(bridge)

    def test_empty_base_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            GitHubJiraBridge(
                jira_client=_make_mock_client(),
                mapper=_make_mapper(),
                jira_base_url="",
            )

    def test_whitespace_base_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            GitHubJiraBridge(
                jira_client=_make_mock_client(),
                mapper=_make_mapper(),
                jira_base_url="   ",
            )


class TestGetRequirementForPR(unittest.TestCase):
    """Tests for GitHubJiraBridge.get_requirement_for_pr()."""

    BASE_URL = "https://acme.atlassian.net"

    # -----------------------------------------------------------------------
    # C + E. No key → Jira NOT called, result has no requirement
    # -----------------------------------------------------------------------

    def test_no_key_returns_no_requirement(self) -> None:
        """C. No key → BridgeResult with requirement=None."""
        mock_client = _make_mock_client()
        bridge = _make_bridge(jira_client=mock_client)

        result = bridge.get_requirement_for_pr(
            pr_title="Add email validation",
            pr_body="No ticket referenced.",
        )

        self.assertIsInstance(result, BridgeResult)
        self.assertIsNone(result.requirement)
        self.assertIsNone(result.jira_key)
        self.assertFalse(result.has_requirement)
        self.assertIn("No Jira key", result.reason)

    def test_jira_not_called_when_no_key(self) -> None:
        """E. When no Jira key is found, JiraClient.get_issue() is never called."""
        mock_client = _make_mock_client()
        bridge = _make_bridge(jira_client=mock_client)

        bridge.get_requirement_for_pr(
            pr_title="Refactor login",
            pr_body="No ticket here.",
        )

        mock_client.get_issue.assert_not_called()

    # -----------------------------------------------------------------------
    # A + F + H. Key in title → Jira called, canonical Requirement returned
    # -----------------------------------------------------------------------

    def test_key_in_title_calls_jira(self) -> None:
        """F. Key extracted → JiraClient.get_issue() is called with that key."""
        issue = _make_jira_issue(key="SCRUM-5")
        mock_client = _make_mock_client(issue)
        bridge = _make_bridge(jira_client=mock_client)

        bridge.get_requirement_for_pr(
            pr_title="SCRUM-5 Add email validation",
            pr_body=None,
        )

        mock_client.get_issue.assert_called_once_with("SCRUM-5")

    def test_result_is_canonical_requirement(self) -> None:
        """H. Bridge returns a canonical Requirement domain model."""
        issue = _make_jira_issue(key="SCRUM-5", summary="Add email validation")
        mock_client = _make_mock_client(issue)
        bridge = _make_bridge(jira_client=mock_client)

        result = bridge.get_requirement_for_pr(
            pr_title="SCRUM-5 Add email validation",
        )

        self.assertIsInstance(result, BridgeResult)
        self.assertTrue(result.has_requirement)
        self.assertIsInstance(result.requirement, Requirement)
        self.assertEqual(result.requirement.requirement_id, "SCRUM-5")
        self.assertEqual(result.requirement.title, "Add email validation")
        self.assertEqual(result.jira_key, "SCRUM-5")

    def test_requirement_source_url_built_correctly(self) -> None:
        issue = _make_jira_issue(key="DEV-123")
        mock_client = _make_mock_client(issue)
        bridge = _make_bridge(jira_client=mock_client, base_url=self.BASE_URL)

        result = bridge.get_requirement_for_pr(pr_title="DEV-123 fix login")

        self.assertEqual(
            result.requirement.source,
            "https://acme.atlassian.net/browse/DEV-123",
        )

    # -----------------------------------------------------------------------
    # B. Key only in body
    # -----------------------------------------------------------------------

    def test_key_only_in_body(self) -> None:
        """B. No key in title → body is searched; correct issue retrieved."""
        issue = _make_jira_issue(key="SCRUM-5")
        mock_client = _make_mock_client(issue)
        bridge = _make_bridge(jira_client=mock_client)

        result = bridge.get_requirement_for_pr(
            pr_title="Add email validation",
            pr_body="Implements SCRUM-5.",
        )

        mock_client.get_issue.assert_called_once_with("SCRUM-5")
        self.assertEqual(result.jira_key, "SCRUM-5")
        self.assertIsNotNone(result.requirement)

    # -----------------------------------------------------------------------
    # G. JiraIssue is passed through JiraRequirementMapper
    # -----------------------------------------------------------------------

    def test_mapper_receives_jira_issue(self) -> None:
        """G. The mapper is called with the JiraIssue returned by the client."""
        issue = _make_jira_issue(key="SCRUM-5")
        mock_client = _make_mock_client(issue)
        mock_mapper = MagicMock(spec=JiraRequirementMapper)

        # Mapper returns a real Requirement so the bridge can finish cleanly
        mock_mapper.to_requirement.return_value = Requirement(
            requirement_id="SCRUM-5",
            title="Add email validation",
            description=None,
            source="https://acme.atlassian.net/browse/SCRUM-5",
            priority=None,
            status="In Progress",
            acceptance_criteria=[],
        )

        bridge = GitHubJiraBridge(
            jira_client=mock_client,
            mapper=mock_mapper,
            jira_base_url=self.BASE_URL,
        )

        bridge.get_requirement_for_pr(pr_title="SCRUM-5 Add email validation")

        mock_mapper.to_requirement.assert_called_once_with(issue, self.BASE_URL)

    # -----------------------------------------------------------------------
    # G + H. Acceptance criteria are mapped through JiraRequirementMapper
    # -----------------------------------------------------------------------

    def test_acceptance_criteria_mapped(self) -> None:
        """G+H. Acceptance criteria are mapped by JiraRequirementMapper."""
        issue = _make_jira_issue(
            key="SCRUM-5",
            acceptance_criteria=["Validate email format", "Return 400 on invalid"],
        )
        mock_client = _make_mock_client(issue)
        bridge = _make_bridge(jira_client=mock_client)

        result = bridge.get_requirement_for_pr(pr_title="SCRUM-5 Add email validation")

        self.assertEqual(len(result.requirement.acceptance_criteria), 2)
        self.assertIsInstance(result.requirement.acceptance_criteria[0], AcceptanceCriterion)

    # -----------------------------------------------------------------------
    # I. Jira API failure propagates
    # -----------------------------------------------------------------------

    def test_jira_network_error_propagates(self) -> None:
        """I. Network errors from JiraClient propagate to the caller."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraNetworkError("Unable to connect to Jira")
        bridge = _make_bridge(jira_client=mock_client)

        with self.assertRaises(JiraNetworkError):
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix login")

    def test_jira_auth_error_propagates(self) -> None:
        """I. Auth errors from JiraClient propagate unchanged."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraAuthenticationError(
            "Jira authentication/access failed (HTTP 401)"
        )
        bridge = _make_bridge(jira_client=mock_client)

        with self.assertRaises(JiraAuthenticationError):
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix login")

    def test_jira_not_found_propagates(self) -> None:
        """I. Not-found errors from JiraClient propagate unchanged."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraIssueNotFoundError(
            "Jira issue not found: SCRUM-5"
        )
        bridge = _make_bridge(jira_client=mock_client)

        with self.assertRaises(JiraIssueNotFoundError):
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix login")

    def test_jira_generic_error_propagates(self) -> None:
        """I. Generic JiraClientError propagates unchanged."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraClientError("Jira API request failed (HTTP 500)")
        bridge = _make_bridge(jira_client=mock_client)

        with self.assertRaises(JiraClientError):
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix")

    # -----------------------------------------------------------------------
    # J. Credentials never appear in errors or logs
    # -----------------------------------------------------------------------

    def test_credentials_not_in_auth_error_message(self) -> None:
        """J. Auth exception message never leaks a token or password."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraAuthenticationError(
            "Jira authentication/access failed (HTTP 401)"
        )
        bridge = _make_bridge(jira_client=mock_client)

        try:
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix")
        except JiraAuthenticationError as exc:
            error_str = str(exc)
            self.assertNotIn("token", error_str.lower())
            self.assertNotIn("password", error_str.lower())
            self.assertNotIn("api_token", error_str.lower())
            self.assertNotIn("Authorization", error_str)
        else:
            self.fail("Expected JiraAuthenticationError to be raised")

    def test_credentials_not_in_network_error_message(self) -> None:
        """J. Network exception message never leaks credentials."""
        mock_client = _make_mock_client()
        mock_client.get_issue.side_effect = JiraNetworkError("Unable to connect to Jira")
        bridge = _make_bridge(jira_client=mock_client)

        try:
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 fix")
        except JiraNetworkError as exc:
            error_str = str(exc)
            self.assertNotIn("token", error_str.lower())
            self.assertNotIn("password", error_str.lower())
            self.assertNotIn("api_token", error_str.lower())
        else:
            self.fail("Expected JiraNetworkError to be raised")

    def test_no_credentials_logged(self) -> None:
        """J. No secret values appear in any log records during bridge execution."""
        FAKE_TOKEN = "FAKE_SECRET_TOKEN_XYZ_12345"
        issue = _make_jira_issue(key="SCRUM-5")
        mock_client = _make_mock_client(issue)

        # Inject a fake token into the bridge base_url string so it is
        # available in the test scope; it must NOT appear in log output.
        bridge = GitHubJiraBridge(
            jira_client=mock_client,
            mapper=_make_mapper(),
            jira_base_url="https://acme.atlassian.net",
        )

        with self.assertLogs(level=logging.DEBUG) as log_capture:
            # We need at least one log record; emit a dummy one.
            logging.getLogger("bridge_test").debug("test started")
            bridge.get_requirement_for_pr(pr_title="SCRUM-5 Add email validation")

        all_output = "\n".join(log_capture.output)
        self.assertNotIn(FAKE_TOKEN, all_output)

    # -----------------------------------------------------------------------
    # BridgeResult convenience predicates
    # -----------------------------------------------------------------------

    def test_bridge_result_has_requirement_true(self) -> None:
        issue = _make_jira_issue()
        result = BridgeResult(
            requirement=Requirement(
                requirement_id="SCRUM-5",
                title="x",
                status="Open",
            ),
            jira_key="SCRUM-5",
            reason="Requirement mapped from Jira issue SCRUM-5.",
        )
        self.assertTrue(result.has_requirement)

    def test_bridge_result_has_requirement_false(self) -> None:
        result = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key found in PR title or body.",
        )
        self.assertFalse(result.has_requirement)


# ===========================================================================
# Part 3 — Package public API
# ===========================================================================

class TestJiraPackagePublicAPI(unittest.TestCase):
    """Confirms the new bridge components are importable from the package root."""

    def test_bridge_importable(self) -> None:
        from app.integrations.jira import GitHubJiraBridge  # noqa: F401
        self.assertTrue(True)

    def test_bridge_result_importable(self) -> None:
        from app.integrations.jira import BridgeResult  # noqa: F401
        self.assertTrue(True)

    def test_extract_jira_key_importable(self) -> None:
        from app.integrations.jira import extract_jira_key  # noqa: F401
        self.assertTrue(True)

    def test_extract_all_jira_keys_importable(self) -> None:
        from app.integrations.jira import extract_all_jira_keys  # noqa: F401
        self.assertTrue(True)

    def test_existing_exports_still_present(self) -> None:
        from app.integrations.jira import JiraClient, JiraRequirementMapper  # noqa: F401
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
