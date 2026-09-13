"""
DevSense — Unit Tests for V1 Domain Models
==========================================
Tests the normalized data contracts:
  - Commit
  - ChangedFile
  - PullRequest
  - JiraIssue

All tests run offline using Python's standard unittest library.
"""

import unittest
from pydantic import ValidationError

from app.domain import ChangedFile, Commit, JiraIssue, PullRequest


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


if __name__ == "__main__":
    unittest.main()

