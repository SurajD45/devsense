"""
DevSense — Unit Tests for V1 Domain Models
==========================================
Tests the normalized data contracts:
  - Commit
  - ChangedFile
  - PullRequest
  - JiraIssue
  - AcceptanceCriterion
  - Requirement

All tests run offline using Python's standard unittest library.
"""

import unittest
from datetime import datetime, timezone
from pydantic import ValidationError

from app.domain import (
    AcceptanceCriterion,
    ChangedFile,
    Commit,
    Evidence,
    EvidenceType,
    Finding,
    FindingStatus,
    Investigation,
    InvestigationStatus,
    JiraIssue,
    PullRequest,
    Requirement,
)


class TestCommitModel(unittest.TestCase):
    """Tests for the Commit domain model."""

    def test_valid_full_sha(self):
        commit = Commit(
            sha="a" * 40,
            message="feat: add core functionality",
            author="octocat",
        )
        self.assertEqual(commit.sha, "a" * 40)
        self.assertEqual(commit.message, "feat: add core functionality")
        self.assertEqual(commit.author, "octocat")

    def test_valid_short_sha(self):
        commit = Commit(
            sha="a1b2c3d",
            message="fix: resolve typo",
            author="developer",
        )
        self.assertEqual(commit.sha, "a1b2c3d")

    def test_sha_surrounding_whitespace_stripped_and_lowercased(self):
        commit = Commit(
            sha="  A1B2C3D  ",
            message="test: whitespace test",
            author="tester",
        )
        self.assertEqual(commit.sha, "a1b2c3d")

    def test_missing_sha_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(message="msg", author="author")

    def test_invalid_non_hex_sha_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="zzzzzzz", message="msg", author="author")

    def test_sha_shorter_than_7_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a1b2c", message="msg", author="author")

    def test_sha_longer_than_40_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a" * 41, message="msg", author="author")

    def test_missing_message_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a1b2c3d", author="author")

    def test_empty_or_whitespace_message_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a1b2c3d", message="   ", author="author")

    def test_missing_author_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a1b2c3d", message="msg")

    def test_empty_or_whitespace_author_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Commit(sha="a1b2c3d", message="msg", author="   ")


class TestChangedFileModel(unittest.TestCase):
    """Tests for the ChangedFile domain model."""

    def test_valid_model(self):
        cf = ChangedFile(
            path="app/domain/models.py",
            status="modified",
            additions=12,
            deletions=3,
            patch="@@ -1,3 +1,12 @@",
        )
        self.assertEqual(cf.path, "app/domain/models.py")
        self.assertEqual(cf.status, "modified")
        self.assertEqual(cf.additions, 12)
        self.assertEqual(cf.deletions, 3)
        self.assertEqual(cf.patch, "@@ -1,3 +1,12 @@")

    def test_default_additions_and_deletions(self):
        cf = ChangedFile(path="README.md", status="added")
        self.assertEqual(cf.additions, 0)
        self.assertEqual(cf.deletions, 0)
        self.assertIsNone(cf.patch)

    def test_negative_additions_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFile(path="README.md", status="modified", additions=-1)

    def test_negative_deletions_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFile(path="README.md", status="modified", deletions=-5)

    def test_optional_patch_omitted_is_valid(self):
        cf = ChangedFile(path="binary.png", status="added", patch=None)
        self.assertIsNone(cf.patch)

    def test_optional_patch_supplied(self):
        cf = ChangedFile(path="main.py", status="modified", patch="+new_line")
        self.assertEqual(cf.patch, "+new_line")

    def test_required_path_rejected_when_empty(self):
        with self.assertRaises(ValidationError):
            ChangedFile(path="   ", status="modified")

    def test_required_status_rejected_when_empty(self):
        with self.assertRaises(ValidationError):
            ChangedFile(path="main.py", status="   ")

    def test_status_normalized_to_lowercase(self):
        cf = ChangedFile(path="main.py", status="  MODIFIED  ")
        self.assertEqual(cf.status, "modified")


class TestPullRequestModel(unittest.TestCase):
    """Tests for the PullRequest domain model."""

    def test_valid_model(self):
        pr = PullRequest(
            repository="owner/repo",
            number=42,
            title="feat: implement domain models",
            author="alice",
            source_branch="feature/domain-models",
            target_branch="main",
            state="open",
        )
        self.assertEqual(pr.repository, "owner/repo")
        self.assertEqual(pr.number, 42)
        self.assertEqual(pr.title, "feat: implement domain models")
        self.assertEqual(pr.author, "alice")
        self.assertEqual(pr.source_branch, "feature/domain-models")
        self.assertEqual(pr.target_branch, "main")
        self.assertEqual(pr.state, "open")
        self.assertIsNone(pr.description)
        self.assertEqual(pr.commits, [])
        self.assertEqual(pr.changed_files, [])

    def test_optional_description_supplied(self):
        pr = PullRequest(
            repository="owner/repo",
            number=1,
            title="title",
            description="  detailed description  ",
            author="alice",
            source_branch="feat",
            target_branch="main",
            state="open",
        )
        self.assertEqual(pr.description, "detailed description")

    def test_default_empty_commits_and_changed_files(self):
        pr1 = PullRequest(
            repository="owner/repo",
            number=1,
            title="t1",
            author="alice",
            source_branch="f1",
            target_branch="main",
            state="open",
        )
        pr2 = PullRequest(
            repository="owner/repo",
            number=2,
            title="t2",
            author="bob",
            source_branch="f2",
            target_branch="main",
            state="open",
        )
        self.assertEqual(pr1.commits, [])
        self.assertEqual(pr1.changed_files, [])
        # Ensure default_factory creates separate lists
        pr1.commits.append(
            Commit(sha="a1b2c3d", message="commit1", author="alice")
        )
        self.assertEqual(len(pr1.commits), 1)
        self.assertEqual(len(pr2.commits), 0)

    def test_invalid_non_positive_number_rejected(self):
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=0,
                title="t",
                author="a",
                source_branch="s",
                target_branch="b",
                state="open",
            )
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=-5,
                title="t",
                author="a",
                source_branch="s",
                target_branch="b",
                state="open",
            )

    def test_repository_must_contain_slash(self):
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="invalid-repo-without-slash",
                number=1,
                title="t",
                author="a",
                source_branch="s",
                target_branch="b",
                state="open",
            )

    def test_missing_required_fields_rejected(self):
        with self.assertRaises(ValidationError):
            PullRequest(repository="owner/repo", number=1)

    def test_empty_required_strings_rejected(self):
        # Empty title
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=1,
                title="   ",
                author="a",
                source_branch="s",
                target_branch="b",
                state="open",
            )
        # Empty author
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=1,
                title="t",
                author="   ",
                source_branch="s",
                target_branch="b",
                state="open",
            )

    def test_nested_valid_commits(self):
        commit = Commit(sha="1234567", message="init", author="alice")
        pr = PullRequest(
            repository="owner/repo",
            number=1,
            title="t",
            author="alice",
            source_branch="s",
            target_branch="main",
            state="open",
            commits=[commit],
        )
        self.assertEqual(len(pr.commits), 1)
        self.assertEqual(pr.commits[0].sha, "1234567")

    def test_nested_valid_changed_files(self):
        cf = ChangedFile(path="file.py", status="added", additions=5)
        pr = PullRequest(
            repository="owner/repo",
            number=1,
            title="t",
            author="alice",
            source_branch="s",
            target_branch="main",
            state="open",
            changed_files=[cf],
        )
        self.assertEqual(len(pr.changed_files), 1)
        self.assertEqual(pr.changed_files[0].path, "file.py")

    def test_invalid_nested_commit_rejected(self):
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=1,
                title="t",
                author="alice",
                source_branch="s",
                target_branch="main",
                state="open",
                commits=[{"sha": "invalid-sha"}],
            )

    def test_invalid_nested_changed_file_rejected(self):
        with self.assertRaises(ValidationError):
            PullRequest(
                repository="owner/repo",
                number=1,
                title="t",
                author="alice",
                source_branch="s",
                target_branch="main",
                state="open",
                changed_files=[{"path": "file.py", "additions": -10}],
            )


class TestJiraIssueModel(unittest.TestCase):
    """Tests for the JiraIssue domain model."""

    def test_valid_model(self):
        issue = JiraIssue(
            issue_key="DEVS-101",
            summary="Add domain models",
            status="In Progress",
            issue_type="Story",
        )
        self.assertEqual(issue.issue_key, "DEVS-101")
        self.assertEqual(issue.summary, "Add domain models")
        self.assertEqual(issue.status, "In Progress")
        self.assertEqual(issue.issue_type, "Story")
        self.assertIsNone(issue.description)
        self.assertIsNone(issue.priority)
        self.assertIsNone(issue.acceptance_criteria)

    def test_valid_issue_key_patterns(self):
        valid_keys = ["PROJ-1", "DEVS_APP-9999", "A1-42", "TEST_KEY-100"]
        for key in valid_keys:
            issue = JiraIssue(
                issue_key=key,
                summary="Summary",
                status="To Do",
                issue_type="Task",
            )
            self.assertEqual(issue.issue_key, key.upper())

    def test_issue_key_lowercased_gets_uppercased(self):
        issue = JiraIssue(
            issue_key="devs-123",
            summary="Summary",
            status="To Do",
            issue_type="Task",
        )
        self.assertEqual(issue.issue_key, "DEVS-123")

    def test_invalid_issue_key_rejected(self):
        invalid_keys = ["123", "no-dash", "PROJ-", "-123", "PROJ-ABC", "   "]
        for key in invalid_keys:
            with self.assertRaises(ValidationError, msg=f"Failed for key: {key}"):
                JiraIssue(
                    issue_key=key,
                    summary="Summary",
                    status="To Do",
                    issue_type="Task",
                )

    def test_optional_fields_supplied(self):
        issue = JiraIssue(
            issue_key="DEVS-42",
            summary="Bug fix",
            description="Detailed bug report",
            status="Open",
            priority="High",
            issue_type="Bug",
            acceptance_criteria="Must pass all regression tests",
        )
        self.assertEqual(issue.description, "Detailed bug report")
        self.assertEqual(issue.priority, "High")
        self.assertEqual(issue.acceptance_criteria, "Must pass all regression tests")

    def test_missing_required_fields_rejected(self):
        with self.assertRaises(ValidationError):
            JiraIssue(issue_key="DEVS-1", summary="Only summary")

    def test_empty_required_strings_rejected(self):
        with self.assertRaises(ValidationError):
            JiraIssue(
                issue_key="DEVS-1",
                summary="   ",
                status="Open",
                issue_type="Task",
            )
        with self.assertRaises(ValidationError):
            JiraIssue(
                issue_key="DEVS-1",
                summary="Summary",
                status="   ",
                issue_type="Task",
            )
        with self.assertRaises(ValidationError):
            JiraIssue(
                issue_key="DEVS-1",
                summary="Summary",
                status="Open",
                issue_type="   ",
            )


# =========================================================================
# Tests for NEW V2 Domain Models: AcceptanceCriterion & Requirement
# =========================================================================


class TestAcceptanceCriterionModel(unittest.TestCase):
    """Tests for the AcceptanceCriterion domain model."""

    def test_valid_minimal(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-1",
            title="User can log in with valid credentials",
        )
        self.assertEqual(ac.criterion_id, "AC-1")
        self.assertEqual(ac.title, "User can log in with valid credentials")
        self.assertIsNone(ac.description)
        self.assertFalse(ac.is_met)

    def test_valid_with_all_fields(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-42",
            title="API returns 200 on success",
            description="The endpoint must return HTTP 200 with a JSON body.",
            is_met=True,
        )
        self.assertEqual(ac.criterion_id, "AC-42")
        self.assertEqual(ac.title, "API returns 200 on success")
        self.assertEqual(ac.description, "The endpoint must return HTTP 200 with a JSON body.")
        self.assertTrue(ac.is_met)

    def test_is_met_defaults_to_false(self):
        ac = AcceptanceCriterion(criterion_id="AC-1", title="Check default")
        self.assertFalse(ac.is_met)

    def test_title_whitespace_stripped(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-1",
            title="  spaces around title  ",
        )
        self.assertEqual(ac.title, "spaces around title")

    def test_description_whitespace_stripped(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-1",
            title="title",
            description="  padded description  ",
        )
        self.assertEqual(ac.description, "padded description")

    def test_description_whitespace_only_becomes_none(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-1",
            title="title",
            description="   ",
        )
        self.assertIsNone(ac.description)

    def test_missing_criterion_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            AcceptanceCriterion(title="title")

    def test_missing_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            AcceptanceCriterion(criterion_id="AC-1")

    def test_empty_criterion_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            AcceptanceCriterion(criterion_id="   ", title="title")

    def test_empty_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            AcceptanceCriterion(criterion_id="AC-1", title="   ")

    def test_valid_criterion_id_patterns(self):
        valid_ids = ["AC-1", "AC-42", "AC-999", "AC-0"]
        for cid in valid_ids:
            ac = AcceptanceCriterion(criterion_id=cid, title="test")
            self.assertEqual(ac.criterion_id, cid)

    def test_invalid_criterion_id_patterns(self):
        invalid_ids = [
            "ac-1",       # lowercase
            "AC1",        # missing dash
            "AC-",        # missing number
            "AC-abc",     # non-numeric suffix
            "1",          # just a number
            "CRIT-1",     # wrong prefix
            "AC -1",      # space in id
            "AC-1.0",     # decimal
        ]
        for cid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Failed for id: {cid}"):
                AcceptanceCriterion(criterion_id=cid, title="test")

    def test_model_dump_structure(self):
        ac = AcceptanceCriterion(
            criterion_id="AC-3",
            title="Schema validation",
            description="Inputs are validated",
            is_met=True,
        )
        data = ac.model_dump()
        self.assertEqual(data["criterion_id"], "AC-3")
        self.assertEqual(data["title"], "Schema validation")
        self.assertEqual(data["description"], "Inputs are validated")
        self.assertTrue(data["is_met"])

    def test_model_dump_defaults(self):
        ac = AcceptanceCriterion(criterion_id="AC-1", title="Minimal")
        data = ac.model_dump()
        self.assertIsNone(data["description"])
        self.assertFalse(data["is_met"])


class TestRequirementModel(unittest.TestCase):
    """Tests for the Requirement domain model."""

    def test_valid_minimal(self):
        req = Requirement(
            requirement_id="REQ-001",
            title="User Authentication",
            status="Draft",
        )
        self.assertEqual(req.requirement_id, "REQ-001")
        self.assertEqual(req.title, "User Authentication")
        self.assertIsNone(req.description)
        self.assertIsNone(req.source)
        self.assertIsNone(req.priority)
        self.assertEqual(req.status, "Draft")
        self.assertEqual(req.acceptance_criteria, [])

    def test_valid_with_all_fields(self):
        ac1 = AcceptanceCriterion(criterion_id="AC-1", title="Login works")
        ac2 = AcceptanceCriterion(criterion_id="AC-2", title="Logout works")
        req = Requirement(
            requirement_id="REQ-AUTH-01",
            title="Authentication Flow",
            description="Full login/logout flow for users",
            source="JIRA:DEVS-101",
            priority="High",
            status="In Progress",
            acceptance_criteria=[ac1, ac2],
        )
        self.assertEqual(req.requirement_id, "REQ-AUTH-01")
        self.assertEqual(req.title, "Authentication Flow")
        self.assertEqual(req.description, "Full login/logout flow for users")
        self.assertEqual(req.source, "JIRA:DEVS-101")
        self.assertEqual(req.priority, "High")
        self.assertEqual(req.status, "In Progress")
        self.assertEqual(len(req.acceptance_criteria), 2)
        self.assertEqual(req.acceptance_criteria[0].criterion_id, "AC-1")
        self.assertEqual(req.acceptance_criteria[1].criterion_id, "AC-2")

    def test_title_whitespace_stripped(self):
        req = Requirement(
            requirement_id="REQ-1",
            title="  padded title  ",
            status="Draft",
        )
        self.assertEqual(req.title, "padded title")

    def test_requirement_id_whitespace_stripped(self):
        req = Requirement(
            requirement_id="  REQ-1  ",
            title="title",
            status="Draft",
        )
        self.assertEqual(req.requirement_id, "REQ-1")

    def test_description_whitespace_only_becomes_none(self):
        req = Requirement(
            requirement_id="REQ-1",
            title="title",
            description="   ",
            status="Draft",
        )
        self.assertIsNone(req.description)

    def test_source_whitespace_only_becomes_none(self):
        req = Requirement(
            requirement_id="REQ-1",
            title="title",
            source="   ",
            status="Draft",
        )
        self.assertIsNone(req.source)

    def test_priority_whitespace_only_becomes_none(self):
        req = Requirement(
            requirement_id="REQ-1",
            title="title",
            priority="   ",
            status="Draft",
        )
        self.assertIsNone(req.priority)

    def test_missing_requirement_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(title="title", status="Draft")

    def test_missing_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(requirement_id="REQ-1", status="Draft")

    def test_missing_status_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(requirement_id="REQ-1", title="title")

    def test_empty_requirement_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(requirement_id="   ", title="title", status="Draft")

    def test_empty_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(requirement_id="REQ-1", title="   ", status="Draft")

    def test_empty_status_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Requirement(requirement_id="REQ-1", title="title", status="   ")

    def test_default_acceptance_criteria_independent_lists(self):
        """Ensure default_factory creates separate lists for each instance."""
        req1 = Requirement(
            requirement_id="REQ-1", title="First", status="Draft",
        )
        req2 = Requirement(
            requirement_id="REQ-2", title="Second", status="Draft",
        )
        self.assertEqual(req1.acceptance_criteria, [])
        self.assertEqual(req2.acceptance_criteria, [])
        req1.acceptance_criteria.append(
            AcceptanceCriterion(criterion_id="AC-1", title="Only for req1")
        )
        self.assertEqual(len(req1.acceptance_criteria), 1)
        self.assertEqual(len(req2.acceptance_criteria), 0)

    def test_nested_invalid_acceptance_criterion_rejected(self):
        with self.assertRaises(ValidationError):
            Requirement(
                requirement_id="REQ-1",
                title="title",
                status="Draft",
                acceptance_criteria=[
                    {"criterion_id": "INVALID", "title": "bad id format"},
                ],
            )

    def test_nested_acceptance_criterion_missing_title_rejected(self):
        with self.assertRaises(ValidationError):
            Requirement(
                requirement_id="REQ-1",
                title="title",
                status="Draft",
                acceptance_criteria=[
                    {"criterion_id": "AC-1"},
                ],
            )

    def test_multiple_acceptance_criteria(self):
        criteria = [
            AcceptanceCriterion(criterion_id=f"AC-{i}", title=f"Criterion {i}")
            for i in range(1, 6)
        ]
        req = Requirement(
            requirement_id="REQ-MULTI",
            title="Multi-criteria requirement",
            status="Open",
            acceptance_criteria=criteria,
        )
        self.assertEqual(len(req.acceptance_criteria), 5)
        for i, ac in enumerate(req.acceptance_criteria, start=1):
            self.assertEqual(ac.criterion_id, f"AC-{i}")
            self.assertEqual(ac.title, f"Criterion {i}")

    def test_model_dump_structure(self):
        ac = AcceptanceCriterion(criterion_id="AC-1", title="Criterion one")
        req = Requirement(
            requirement_id="REQ-DUMP",
            title="Dump test",
            description="Testing serialization",
            source="manual",
            priority="Medium",
            status="Active",
            acceptance_criteria=[ac],
        )
        data = req.model_dump()
        self.assertEqual(data["requirement_id"], "REQ-DUMP")
        self.assertEqual(data["title"], "Dump test")
        self.assertEqual(data["description"], "Testing serialization")
        self.assertEqual(data["source"], "manual")
        self.assertEqual(data["priority"], "Medium")
        self.assertEqual(data["status"], "Active")
        self.assertIsInstance(data["acceptance_criteria"], list)
        self.assertEqual(len(data["acceptance_criteria"]), 1)
        self.assertEqual(data["acceptance_criteria"][0]["criterion_id"], "AC-1")

    def test_model_dump_defaults(self):
        req = Requirement(
            requirement_id="REQ-1",
            title="Minimal",
            status="Draft",
        )
        data = req.model_dump()
        self.assertIsNone(data["description"])
        self.assertIsNone(data["source"])
        self.assertIsNone(data["priority"])
        self.assertEqual(data["acceptance_criteria"], [])

    def test_source_is_freeform_string(self):
        """Source accepts any non-empty string — it is provider-independent."""
        for source in ["JIRA:DEVS-42", "GitHub#123", "manual-entry", "Confluence"]:
            req = Requirement(
                requirement_id="REQ-1",
                title="title",
                status="Draft",
                source=source,
            )
            self.assertEqual(req.source, source)


# =========================================================================
# Tests for Evidence Domain Model
# =========================================================================


class TestEvidenceModel(unittest.TestCase):
    """Tests for the Evidence domain model."""

    # -- helpers --

    def _make_code_evidence(self, **overrides):
        """Return kwargs for a valid CODE evidence, with overrides applied."""
        defaults = dict(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            repository="owner/repo",
            file_path="app/domain/models.py",
            start_line=10,
            end_line=25,
            symbol="Commit",
            commit_sha="abc1234",
            description="Commit model definition",
        )
        defaults.update(overrides)
        return defaults

    # -- valid construction --

    def test_valid_code_evidence(self):
        ev = Evidence(**self._make_code_evidence())
        self.assertEqual(ev.evidence_id, "EV-1")
        self.assertEqual(ev.evidence_type, EvidenceType.CODE)
        self.assertEqual(ev.repository, "owner/repo")
        self.assertEqual(ev.file_path, "app/domain/models.py")
        self.assertEqual(ev.start_line, 10)
        self.assertEqual(ev.end_line, 25)
        self.assertEqual(ev.symbol, "Commit")
        self.assertEqual(ev.commit_sha, "abc1234")
        self.assertEqual(ev.description, "Commit model definition")

    def test_valid_test_evidence(self):
        ev = Evidence(
            evidence_id="EV-2",
            evidence_type=EvidenceType.TEST,
            file_path="tests/domain/test_models.py",
            description="Unit test for Commit model",
        )
        self.assertEqual(ev.evidence_type, EvidenceType.TEST)
        self.assertEqual(ev.file_path, "tests/domain/test_models.py")

    def test_valid_documentation_evidence(self):
        ev = Evidence(
            evidence_id="EV-3",
            evidence_type=EvidenceType.DOCUMENTATION,
            file_path="docs/architecture.md",
            description="Architecture decision record",
        )
        self.assertEqual(ev.evidence_type, EvidenceType.DOCUMENTATION)

    def test_valid_ci_evidence(self):
        ev = Evidence(
            evidence_id="EV-4",
            evidence_type=EvidenceType.CI,
            file_path=".github/workflows/ci.yml",
            description="CI pipeline definition",
        )
        self.assertEqual(ev.evidence_type, EvidenceType.CI)

    def test_valid_requirement_evidence_without_file_path(self):
        """REQUIREMENT evidence may reference a requirement by description alone."""
        ev = Evidence(
            evidence_id="EV-5",
            evidence_type=EvidenceType.REQUIREMENT,
            description="Requirement REQ-AUTH-01 mandates OAuth2 support",
        )
        self.assertEqual(ev.evidence_type, EvidenceType.REQUIREMENT)
        self.assertIsNone(ev.file_path)
        self.assertIsNone(ev.repository)
        self.assertIsNone(ev.start_line)
        self.assertIsNone(ev.end_line)
        self.assertIsNone(ev.symbol)
        self.assertIsNone(ev.commit_sha)

    def test_evidence_type_string_values(self):
        for member in EvidenceType:
            ev = Evidence(
                evidence_id="EV-1",
                evidence_type=member,
                description="type test",
            )
            self.assertEqual(ev.evidence_type, member)
            self.assertEqual(ev.evidence_type.value, member.value)

    def test_evidence_type_from_string(self):
        """EvidenceType can be constructed from its raw string value."""
        ev = Evidence(
            evidence_id="EV-1",
            evidence_type="CODE",
            description="string coercion test",
        )
        self.assertEqual(ev.evidence_type, EvidenceType.CODE)

    def test_invalid_evidence_type_rejected(self):
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_id="EV-1",
                evidence_type="INVALID_TYPE",
                description="bad type",
            )

    # -- evidence_id validation --

    def test_valid_evidence_id_patterns(self):
        for eid in ["EV-1", "EV-0", "EV-42", "EV-9999"]:
            ev = Evidence(
                evidence_id=eid,
                evidence_type=EvidenceType.CODE,
                description="id test",
            )
            self.assertEqual(ev.evidence_id, eid)

    def test_invalid_evidence_id_patterns(self):
        invalid_ids = [
            "ev-1",       # lowercase
            "EV1",        # missing dash
            "EV-",        # missing number
            "EV-abc",     # non-numeric suffix
            "ID-1",       # wrong prefix
            "EV -1",      # space in id
            "EV-1.0",     # decimal
            "   ",        # whitespace only
        ]
        for eid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Failed for id: {eid}"):
                Evidence(
                    evidence_id=eid,
                    evidence_type=EvidenceType.CODE,
                    description="id test",
                )

    # -- required field validation --

    def test_missing_evidence_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_type=EvidenceType.CODE,
                description="missing id",
            )

    def test_missing_evidence_type_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_id="EV-1",
                description="missing type",
            )

    def test_missing_description_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_id="EV-1",
                evidence_type=EvidenceType.CODE,
            )

    def test_empty_description_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_id="EV-1",
                evidence_type=EvidenceType.CODE,
                description="   ",
            )

    # -- optional field whitespace normalization --

    def test_repository_whitespace_only_becomes_none(self):
        ev = Evidence(**self._make_code_evidence(repository="   "))
        self.assertIsNone(ev.repository)

    def test_file_path_whitespace_only_becomes_none(self):
        ev = Evidence(**self._make_code_evidence(file_path="   "))
        self.assertIsNone(ev.file_path)

    def test_symbol_whitespace_only_becomes_none(self):
        ev = Evidence(**self._make_code_evidence(symbol="   "))
        self.assertIsNone(ev.symbol)

    def test_commit_sha_whitespace_only_becomes_none(self):
        ev = Evidence(**self._make_code_evidence(commit_sha="   "))
        self.assertIsNone(ev.commit_sha)

    def test_description_whitespace_stripped(self):
        ev = Evidence(**self._make_code_evidence(description="  trimmed  "))
        self.assertEqual(ev.description, "trimmed")

    # -- line number validation --

    def test_start_line_zero_rejected(self):
        with self.assertRaises(ValidationError):
            Evidence(**self._make_code_evidence(start_line=0))

    def test_start_line_negative_rejected(self):
        with self.assertRaises(ValidationError):
            Evidence(**self._make_code_evidence(start_line=-1))

    def test_end_line_zero_rejected(self):
        with self.assertRaises(ValidationError):
            Evidence(**self._make_code_evidence(end_line=0))

    def test_end_line_less_than_start_line_rejected(self):
        with self.assertRaises(ValidationError):
            Evidence(**self._make_code_evidence(start_line=20, end_line=10))

    def test_start_line_equals_end_line_valid(self):
        ev = Evidence(**self._make_code_evidence(start_line=5, end_line=5))
        self.assertEqual(ev.start_line, 5)
        self.assertEqual(ev.end_line, 5)

    def test_end_line_without_start_line_valid(self):
        """end_line alone is valid — no cross-field constraint fires."""
        ev = Evidence(**self._make_code_evidence(start_line=None, end_line=10))
        self.assertIsNone(ev.start_line)
        self.assertEqual(ev.end_line, 10)

    def test_start_line_without_end_line_valid(self):
        ev = Evidence(**self._make_code_evidence(start_line=5, end_line=None))
        self.assertEqual(ev.start_line, 5)
        self.assertIsNone(ev.end_line)

    # -- serialization --

    def test_model_dump_full(self):
        ev = Evidence(**self._make_code_evidence())
        data = ev.model_dump()
        self.assertEqual(data["evidence_id"], "EV-1")
        self.assertEqual(data["evidence_type"], "CODE")
        self.assertEqual(data["repository"], "owner/repo")
        self.assertEqual(data["file_path"], "app/domain/models.py")
        self.assertEqual(data["start_line"], 10)
        self.assertEqual(data["end_line"], 25)
        self.assertEqual(data["symbol"], "Commit")
        self.assertEqual(data["commit_sha"], "abc1234")
        self.assertEqual(data["description"], "Commit model definition")

    def test_model_dump_defaults(self):
        ev = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            description="minimal",
        )
        data = ev.model_dump()
        self.assertIsNone(data["repository"])
        self.assertIsNone(data["file_path"])
        self.assertIsNone(data["start_line"])
        self.assertIsNone(data["end_line"])
        self.assertIsNone(data["symbol"])
        self.assertIsNone(data["commit_sha"])


# =========================================================================
# Tests for Finding Domain Model
# =========================================================================


class TestFindingModel(unittest.TestCase):
    """Tests for the Finding domain model."""

    # -- helpers --

    def _make_finding(self, **overrides):
        """Return kwargs for a valid Finding, with overrides applied."""
        defaults = dict(
            finding_id="FIND-1",
            criterion_id="AC-1",
            status=FindingStatus.VERIFIED,
            title="Login flow verified",
            explanation="All login acceptance criteria are met by test evidence.",
            evidence_ids=["EV-1", "EV-2"],
        )
        defaults.update(overrides)
        return defaults

    # -- valid construction --

    def test_valid_finding(self):
        f = Finding(**self._make_finding())
        self.assertEqual(f.finding_id, "FIND-1")
        self.assertEqual(f.criterion_id, "AC-1")
        self.assertEqual(f.status, FindingStatus.VERIFIED)
        self.assertEqual(f.title, "Login flow verified")
        self.assertEqual(f.explanation, "All login acceptance criteria are met by test evidence.")
        self.assertEqual(f.evidence_ids, ["EV-1", "EV-2"])

    def test_valid_finding_empty_evidence(self):
        f = Finding(**self._make_finding(evidence_ids=[]))
        self.assertEqual(f.evidence_ids, [])

    def test_valid_finding_default_evidence_is_empty_list(self):
        f = Finding(
            finding_id="FIND-1",
            criterion_id="AC-1",
            status=FindingStatus.VERIFIED,
            title="title",
            explanation="explanation",
        )
        self.assertEqual(f.evidence_ids, [])

    # -- FindingStatus enum --

    def test_every_finding_status(self):
        for member in FindingStatus:
            f = Finding(**self._make_finding(status=member))
            self.assertEqual(f.status, member)

    def test_finding_status_from_string(self):
        f = Finding(**self._make_finding(status="PARTIALLY_VERIFIED"))
        self.assertEqual(f.status, FindingStatus.PARTIALLY_VERIFIED)

    def test_invalid_finding_status_rejected(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(status="INVALID_STATUS"))

    # -- finding_id validation --

    def test_valid_finding_id_patterns(self):
        for fid in ["FIND-0", "FIND-1", "FIND-42", "FIND-9999"]:
            f = Finding(**self._make_finding(finding_id=fid))
            self.assertEqual(f.finding_id, fid)

    def test_invalid_finding_id_patterns(self):
        invalid_ids = [
            "find-1",      # lowercase
            "FIND1",       # missing dash
            "FIND-",       # missing number
            "FIND-abc",    # non-numeric suffix
            "FND-1",       # wrong prefix
            "FIND -1",     # space in id
            "FIND-1.0",    # decimal
            "   ",         # whitespace only
        ]
        for fid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Failed for id: {fid}"):
                Finding(**self._make_finding(finding_id=fid))

    def test_missing_finding_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Finding(
                criterion_id="AC-1",
                status=FindingStatus.VERIFIED,
                title="title",
                explanation="explanation",
            )

    # -- criterion_id validation --

    def test_valid_finding_with_criterion_id(self):
        f = Finding(**self._make_finding(criterion_id="AC-1"))
        self.assertEqual(f.criterion_id, "AC-1")

    def test_valid_finding_with_criterion_id_none(self):
        f = Finding(**self._make_finding(criterion_id=None))
        self.assertIsNone(f.criterion_id)

    def test_omitted_criterion_id_defaults_to_none(self):
        kwargs = self._make_finding()
        del kwargs["criterion_id"]
        f = Finding(**kwargs)
        self.assertIsNone(f.criterion_id)

    def test_whitespace_criterion_id_becomes_none(self):
        f = Finding(**self._make_finding(criterion_id="   "))
        self.assertIsNone(f.criterion_id)

    def test_criterion_id_whitespace_stripped(self):
        f = Finding(**self._make_finding(criterion_id="  AC-5  "))
        self.assertEqual(f.criterion_id, "AC-5")

    def test_invalid_criterion_id_type_rejected(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(criterion_id=123))

    # -- title validation --

    def test_title_whitespace_stripped(self):
        f = Finding(**self._make_finding(title="  padded title  "))
        self.assertEqual(f.title, "padded title")

    def test_empty_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(title="   "))

    # -- explanation validation --

    def test_explanation_whitespace_stripped(self):
        f = Finding(**self._make_finding(explanation="  reasoning  "))
        self.assertEqual(f.explanation, "reasoning")

    def test_empty_explanation_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(explanation="   "))

    # -- evidence_ids validation --

    def test_multiple_valid_evidence_ids(self):
        ids = ["EV-1", "EV-2", "EV-100"]
        f = Finding(**self._make_finding(evidence_ids=ids))
        self.assertEqual(f.evidence_ids, ids)

    def test_invalid_evidence_id_in_list_rejected(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(evidence_ids=["EV-1", "INVALID"]))

    def test_all_invalid_evidence_ids_rejected(self):
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(evidence_ids=["BAD-1", "ev-2"]))

    def test_evidence_ids_whitespace_stripped(self):
        f = Finding(**self._make_finding(evidence_ids=["  EV-1  ", "  EV-2  "]))
        self.assertEqual(f.evidence_ids, ["EV-1", "EV-2"])

    # -- default list independence --

    def test_default_evidence_ids_independent_lists(self):
        """Ensure default_factory creates separate lists for each instance."""
        f1_kwargs = self._make_finding()
        del f1_kwargs["evidence_ids"]
        f1 = Finding(**f1_kwargs)
        f2_kwargs = self._make_finding(finding_id="FIND-2")
        del f2_kwargs["evidence_ids"]
        f2 = Finding(**f2_kwargs)
        f1.evidence_ids.append("EV-99")
        self.assertEqual(len(f1.evidence_ids), 1)
        self.assertEqual(len(f2.evidence_ids), 0)

    # -- Evidence objects not embedded --

    def test_evidence_objects_not_embedded(self):
        """Finding stores evidence_ids (strings), not Evidence objects."""
        f = Finding(**self._make_finding())
        for eid in f.evidence_ids:
            self.assertIsInstance(eid, str)
        # Attempting to pass Evidence objects should fail
        ev = Evidence(
            evidence_id="EV-1",
            evidence_type=EvidenceType.CODE,
            description="test",
        )
        with self.assertRaises(ValidationError):
            Finding(**self._make_finding(evidence_ids=[ev]))

    # -- serialization --

    def test_model_dump_full(self):
        f = Finding(**self._make_finding())
        data = f.model_dump()
        self.assertEqual(data["finding_id"], "FIND-1")
        self.assertEqual(data["criterion_id"], "AC-1")
        self.assertEqual(data["status"], "VERIFIED")
        self.assertEqual(data["title"], "Login flow verified")
        self.assertEqual(
            data["explanation"],
            "All login acceptance criteria are met by test evidence.",
        )
        self.assertEqual(data["evidence_ids"], ["EV-1", "EV-2"])

    def test_model_dump_defaults(self):
        f = Finding(
            finding_id="FIND-1",
            status=FindingStatus.NOT_VERIFIED,
            title="Minimal",
            explanation="No evidence found.",
        )
        data = f.model_dump()
        self.assertIsNone(data["criterion_id"])
        self.assertEqual(data["evidence_ids"], [])
        self.assertEqual(data["status"], "NOT_VERIFIED")


# =========================================================================
# Tests for Investigation Domain Model
# =========================================================================


class TestInvestigationModel(unittest.TestCase):
    """Tests for the Investigation domain model."""

    _NOW = datetime(2026, 9, 20, 14, 30, 0, tzinfo=timezone.utc)

    # -- helpers --

    def _make_investigation(self, **overrides):
        """Return kwargs for a valid Investigation, with overrides applied."""
        defaults = dict(
            investigation_id="INV-1",
            pull_request_number=42,
            requirement_id="REQ-AUTH-01",
            status=InvestigationStatus.PENDING,
            finding_ids=["FIND-1", "FIND-2"],
            created_at=self._NOW,
        )
        defaults.update(overrides)
        return defaults

    # -- valid construction --

    def test_valid_investigation(self):
        inv = Investigation(**self._make_investigation())
        self.assertEqual(inv.investigation_id, "INV-1")
        self.assertEqual(inv.pull_request_number, 42)
        self.assertEqual(inv.requirement_id, "REQ-AUTH-01")
        self.assertEqual(inv.status, InvestigationStatus.PENDING)
        self.assertEqual(inv.finding_ids, ["FIND-1", "FIND-2"])
        self.assertEqual(inv.created_at, self._NOW)

    def test_valid_investigation_empty_findings(self):
        inv = Investigation(**self._make_investigation(finding_ids=[]))
        self.assertEqual(inv.finding_ids, [])

    def test_valid_investigation_default_findings_is_empty_list(self):
        kwargs = self._make_investigation()
        del kwargs["finding_ids"]
        inv = Investigation(**kwargs)
        self.assertEqual(inv.finding_ids, [])

    # -- InvestigationStatus enum --

    def test_every_investigation_status(self):
        for member in InvestigationStatus:
            inv = Investigation(**self._make_investigation(status=member))
            self.assertEqual(inv.status, member)

    def test_investigation_status_from_string(self):
        inv = Investigation(**self._make_investigation(status="IN_PROGRESS"))
        self.assertEqual(inv.status, InvestigationStatus.IN_PROGRESS)

    def test_invalid_investigation_status_rejected(self):
        with self.assertRaises(ValidationError):
            Investigation(**self._make_investigation(status="INVALID"))

    # -- investigation_id validation --

    def test_valid_investigation_id_patterns(self):
        for iid in ["INV-0", "INV-1", "INV-42", "INV-9999"]:
            inv = Investigation(**self._make_investigation(investigation_id=iid))
            self.assertEqual(inv.investigation_id, iid)

    def test_invalid_investigation_id_patterns(self):
        invalid_ids = [
            "inv-1",       # lowercase
            "INV1",        # missing dash
            "INV-",        # missing number
            "INV-abc",     # non-numeric suffix
            "INVEST-1",    # wrong prefix
            "INV -1",      # space in id
            "INV-1.0",     # decimal
            "   ",         # whitespace only
        ]
        for iid in invalid_ids:
            with self.assertRaises(ValidationError, msg=f"Failed for id: {iid}"):
                Investigation(**self._make_investigation(investigation_id=iid))

    def test_missing_investigation_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Investigation(
                pull_request_number=1,
                requirement_id="REQ-1",
                status=InvestigationStatus.PENDING,
                created_at=self._NOW,
            )

    # -- pull_request_number validation --

    def test_pull_request_number_one_is_valid(self):
        inv = Investigation(**self._make_investigation(pull_request_number=1))
        self.assertEqual(inv.pull_request_number, 1)

    def test_pull_request_number_zero_rejected(self):
        with self.assertRaises(ValidationError):
            Investigation(**self._make_investigation(pull_request_number=0))

    def test_pull_request_number_negative_rejected(self):
        with self.assertRaises(ValidationError):
            Investigation(**self._make_investigation(pull_request_number=-5))

    # -- requirement_id validation --

    def test_requirement_id_whitespace_stripped(self):
        inv = Investigation(**self._make_investigation(requirement_id="  REQ-1  "))
        self.assertEqual(inv.requirement_id, "REQ-1")

    def test_empty_requirement_id_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Investigation(**self._make_investigation(requirement_id="   "))

    # -- finding_ids validation --

    def test_multiple_valid_finding_ids(self):
        ids = ["FIND-1", "FIND-2", "FIND-100"]
        inv = Investigation(**self._make_investigation(finding_ids=ids))
        self.assertEqual(inv.finding_ids, ids)

    def test_invalid_finding_id_in_list_rejected(self):
        with self.assertRaises(ValidationError):
            Investigation(**self._make_investigation(finding_ids=["FIND-1", "BAD"]))

    def test_finding_ids_whitespace_stripped(self):
        inv = Investigation(**self._make_investigation(
            finding_ids=["  FIND-1  ", "  FIND-2  "],
        ))
        self.assertEqual(inv.finding_ids, ["FIND-1", "FIND-2"])

    # -- default list independence --

    def test_default_finding_ids_independent_lists(self):
        """Ensure default_factory creates separate lists for each instance."""
        k1 = self._make_investigation()
        del k1["finding_ids"]
        k2 = self._make_investigation(investigation_id="INV-2")
        del k2["finding_ids"]
        inv1 = Investigation(**k1)
        inv2 = Investigation(**k2)
        inv1.finding_ids.append("FIND-99")
        self.assertEqual(len(inv1.finding_ids), 1)
        self.assertEqual(len(inv2.finding_ids), 0)

    # -- created_at --

    def test_created_at_accepts_datetime(self):
        inv = Investigation(**self._make_investigation())
        self.assertIsInstance(inv.created_at, datetime)

    def test_created_at_accepts_iso_string(self):
        inv = Investigation(**self._make_investigation(
            created_at="2026-09-20T14:30:00Z",
        ))
        self.assertIsInstance(inv.created_at, datetime)

    # -- serialization --

    def test_model_dump_full(self):
        inv = Investigation(**self._make_investigation())
        data = inv.model_dump()
        self.assertEqual(data["investigation_id"], "INV-1")
        self.assertEqual(data["pull_request_number"], 42)
        self.assertEqual(data["requirement_id"], "REQ-AUTH-01")
        self.assertEqual(data["status"], "PENDING")
        self.assertEqual(data["finding_ids"], ["FIND-1", "FIND-2"])
        self.assertIsInstance(data["created_at"], datetime)

    def test_model_dump_defaults(self):
        kwargs = self._make_investigation()
        del kwargs["finding_ids"]
        inv = Investigation(**kwargs)
        data = inv.model_dump()
        self.assertEqual(data["finding_ids"], [])


if __name__ == "__main__":
    unittest.main()
