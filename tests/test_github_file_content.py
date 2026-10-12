"""
DevSense — Unit Tests for Changed-File Content Retrieval Layer
==============================================================
Verifies retrieval of changed text files pinned to the PR head commit SHA,
handling of deleted files, binary/unsupported formats, 404s, API failures,
and empty file lists without making real network calls.
"""

from __future__ import annotations

import base64
import unittest
from unittest.mock import MagicMock, call, patch

from app.domain.models import ChangedFile
from app.integrations.github.client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubNetworkError,
    GitHubNotFoundError,
)
from app.integrations.github.content import (
    ChangedFileContentBatch,
    FileContentResult,
    FileContentStatus,
    GitHubFileContentService,
)


class TestGitHubFileContentService(unittest.TestCase):
    """Unit tests for GitHubFileContentService."""

    def setUp(self) -> None:
        self.mock_client = MagicMock(spec=GitHubClient)
        self.service = GitHubFileContentService(client=self.mock_client)
        self.repository = "SurajD45/ai-pr-investigator-demo"
        self.head_sha = "6dcb09b5b57875f334f61aebed695e2e4193db5e"

    # -------------------------------------------------------------------------
    # Scenario 1: Successful retrieval of multiple changed files
    # -------------------------------------------------------------------------
    def test_successful_retrieval_of_multiple_changed_files(self) -> None:
        """Eligible text files are retrieved and mapped to their file paths."""
        changed_files = [
            ChangedFile(
                path="app/auth.py",
                status="modified",
                additions=10,
                deletions=2,
                changes=12,
            ),
            ChangedFile(
                path="app/utils.py",
                status="added",
                additions=5,
                deletions=0,
                changes=5,
            ),
        ]

        auth_code = "def authenticate():\n    return True\n"
        utils_code = "def helper():\n    pass\n"

        def mock_get_content(owner: str, repo: str, path: str, ref: str) -> str:
            if path == "app/auth.py":
                return auth_code
            if path == "app/utils.py":
                return utils_code
            raise GitHubNotFoundError(404, f"File {path} not found")

        self.mock_client.get_file_content.side_effect = mock_get_content

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        self.assertIsInstance(batch, ChangedFileContentBatch)
        self.assertEqual(len(batch), 2)
        self.assertEqual(len(batch.contents), 2)
        self.assertEqual(len(batch.failures), 0)

        # Content dictionary mapping
        self.assertEqual(batch.contents["app/auth.py"], auth_code)
        self.assertEqual(batch.contents["app/utils.py"], utils_code)
        self.assertEqual(batch["app/auth.py"], auth_code)
        self.assertEqual(batch.get("app/auth.py"), auth_code)
        self.assertTrue("app/auth.py" in batch)

        # Result item validation
        res_auth = batch.results["app/auth.py"]
        self.assertEqual(res_auth.status, FileContentStatus.SUCCESS)
        self.assertTrue(res_auth.is_success)
        self.assertEqual(res_auth.content, auth_code)
        self.assertEqual(res_auth.size_bytes, len(auth_code.encode("utf-8")))

    # -------------------------------------------------------------------------
    # Scenario 2: Correct use of the PR head commit SHA
    # -------------------------------------------------------------------------
    def test_correct_use_of_pr_head_commit_sha(self) -> None:
        """Verify the PR head commit SHA is strictly used as ref, never default branch."""
        changed_files = [
            ChangedFile(path="app/feature.py", status="modified", additions=1, deletions=1, changes=2)
        ]
        self.mock_client.get_file_content.return_value = "feature_code = True"

        self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        self.mock_client.get_file_content.assert_called_once_with(
            "SurajD45",
            "ai-pr-investigator-demo",
            "app/feature.py",
            ref=self.head_sha,
        )
        # Explicit assertion: ref must NOT be default branches
        called_ref = self.mock_client.get_file_content.call_args.kwargs.get("ref")
        self.assertNotIn(called_ref, ["main", "master", "HEAD", None, ""])
        self.assertEqual(called_ref, self.head_sha)

    # -------------------------------------------------------------------------
    # Scenario 3: Deleted files handling
    # -------------------------------------------------------------------------
    def test_deleted_files_skipped_without_api_call(self) -> None:
        """Deleted or removed files are marked DELETED without calling GitHub API."""
        changed_files = [
            ChangedFile(path="app/deprecated.py", status="removed", additions=0, deletions=50, changes=50),
            ChangedFile(path="app/old_module.py", status="deleted", additions=0, deletions=20, changes=20),
            ChangedFile(path="app/active.py", status="modified", additions=5, deletions=1, changes=6),
        ]

        self.mock_client.get_file_content.return_value = "active_content = 1"

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        # Client should only be called once, for the active file
        self.mock_client.get_file_content.assert_called_once_with(
            "SurajD45", "ai-pr-investigator-demo", "app/active.py", ref=self.head_sha
        )

        # Removed file checks
        deprecated_res = batch.results["app/deprecated.py"]
        self.assertEqual(deprecated_res.status, FileContentStatus.DELETED)
        self.assertIsNone(deprecated_res.content)
        self.assertFalse(deprecated_res.is_success)

        # Deleted file checks
        old_res = batch.results["app/old_module.py"]
        self.assertEqual(old_res.status, FileContentStatus.DELETED)
        self.assertIsNone(old_res.content)
        self.assertFalse(old_res.is_success)

        # Contents map must not contain deleted files or empty strings
        self.assertNotIn("app/deprecated.py", batch.contents)
        self.assertNotIn("app/old_module.py", batch.contents)
        self.assertEqual(batch.contents, {"app/active.py": "active_content = 1"})
        self.assertIn("app/deprecated.py", batch.failures)
        self.assertIn("app/old_module.py", batch.failures)

    # -------------------------------------------------------------------------
    # Scenario 4: Unavailable (404) or unsupported (binary/oversized) content
    # -------------------------------------------------------------------------
    def test_binary_files_skipped_without_api_call(self) -> None:
        """Binary files (.png, .zip, etc.) are skipped without API requests."""
        changed_files = [
            ChangedFile(path="static/logo.png", status="added", additions=0, deletions=0, changes=0),
            ChangedFile(path="archive/bundle.zip", status="modified", additions=0, deletions=0, changes=0),
            ChangedFile(path="docs/spec.pdf", status="modified", additions=0, deletions=0, changes=0),
        ]

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        self.mock_client.get_file_content.assert_not_called()
        self.assertEqual(len(batch.contents), 0)
        self.assertEqual(len(batch.failures), 3)

        for path in ["static/logo.png", "archive/bundle.zip", "docs/spec.pdf"]:
            res = batch.results[path]
            self.assertEqual(res.status, FileContentStatus.BINARY)
            self.assertIsNone(res.content)
            self.assertFalse(res.is_success)
            self.assertFalse(path in batch)

    def test_unavailable_404_file_handled_safely(self) -> None:
        """404 Not Found returns FileContentStatus.NOT_FOUND with content=None, not empty string."""
        changed_files = [
            ChangedFile(path="app/missing.py", status="modified", additions=1, deletions=0, changes=1)
        ]
        self.mock_client.get_file_content.side_effect = GitHubNotFoundError(
            "File 'app/missing.py' not found at ref"
        )

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        res = batch.results["app/missing.py"]
        self.assertEqual(res.status, FileContentStatus.NOT_FOUND)
        self.assertIsNone(res.content)
        self.assertFalse(res.is_success)
        self.assertNotIn("app/missing.py", batch.contents)
        self.assertIn("app/missing.py", batch.failures)
        with self.assertRaises(KeyError):
            _ = batch["app/missing.py"]

    def test_oversized_file_handled_safely(self) -> None:
        """Files exceeding max_file_size_bytes are flagged as OVERSIZED with content=None."""
        service = GitHubFileContentService(
            client=self.mock_client,
            max_file_size_bytes=50,  # 50 bytes limit for test
        )
        changed_files = [
            ChangedFile(path="app/large.py", status="modified", additions=100, deletions=0, changes=100)
        ]
        large_code = "x = 1\n" * 50  # ~300 bytes

        self.mock_client.get_file_content.return_value = large_code

        batch = service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        res = batch.results["app/large.py"]
        self.assertEqual(res.status, FileContentStatus.OVERSIZED)
        self.assertIsNone(res.content)
        self.assertFalse(res.is_success)
        self.assertNotIn("app/large.py", batch.contents)
        self.assertIn("app/large.py", batch.failures)

    # -------------------------------------------------------------------------
    # Scenario 5: GitHub API failure
    # -------------------------------------------------------------------------
    def test_github_api_failure_handled_gracefully(self) -> None:
        """GitHub API errors (500, network errors) are caught and do not crash the service."""
        changed_files = [
            ChangedFile(path="app/error.py", status="modified", additions=2, deletions=1, changes=3),
            ChangedFile(path="app/network_fail.py", status="modified", additions=1, deletions=0, changes=1),
        ]

        def side_effect(owner: str, repo: str, path: str, ref: str) -> str:
            if path == "app/error.py":
                raise GitHubAPIError("Internal Server Error from GitHub API", status_code=500)
            if path == "app/network_fail.py":
                raise GitHubNetworkError("Connection timed out to api.github.com")
            return "ok"

        self.mock_client.get_file_content.side_effect = side_effect

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        self.assertEqual(len(batch.contents), 0)
        self.assertEqual(len(batch.failures), 2)

        error_res = batch.results["app/error.py"]
        self.assertEqual(error_res.status, FileContentStatus.ERROR)
        self.assertIsNone(error_res.content)
        self.assertIn("Internal Server Error", error_res.error or "")

        net_res = batch.results["app/network_fail.py"]
        self.assertEqual(net_res.status, FileContentStatus.ERROR)
        self.assertIsNone(net_res.content)
        self.assertIn("Connection timed out", net_res.error or "")

    # -------------------------------------------------------------------------
    # Scenario 6: Empty changed-file lists
    # -------------------------------------------------------------------------
    def test_empty_changed_file_list(self) -> None:
        """Empty changed-file list produces an empty batch without calling API."""
        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=[],
        )

        self.assertEqual(len(batch), 0)
        self.assertEqual(batch.contents, {})
        self.assertEqual(batch.failures, {})
        self.mock_client.get_file_content.assert_not_called()

    # -------------------------------------------------------------------------
    # Parameter Validation & Edge Cases
    # -------------------------------------------------------------------------
    def test_invalid_repository_format(self) -> None:
        with self.assertRaises(ValueError):
            self.service.retrieve_contents("invalid-repo-without-slash", self.head_sha, [])

        with self.assertRaises(ValueError):
            self.service.retrieve_contents("", self.head_sha, [])

    def test_invalid_commit_sha(self) -> None:
        with self.assertRaises(ValueError):
            self.service.retrieve_contents(self.repository, "not-a-hex-sha!", [])

        with self.assertRaises(ValueError):
            self.service.retrieve_contents(self.repository, "123", [])  # too short (<7 chars)

        with self.assertRaises(ValueError):
            self.service.retrieve_contents(self.repository, "", [])

    def test_no_client_provided(self) -> None:
        service = GitHubFileContentService(client=None)
        with self.assertRaises(ValueError):
            service.retrieve_contents(self.repository, self.head_sha, [])

    def test_dict_input_records_support(self) -> None:
        """Verify service also accepts raw dictionaries (e.g. from raw webhook context)."""
        dict_files = [
            {"filename": "app/main.py", "status": "modified"},
            {"path": "README.md", "status": "added"},
            {"filename": "docs/old.md", "status": "removed"},
        ]
        self.mock_client.get_file_content.side_effect = lambda o, r, p, ref: f"content of {p}"

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=dict_files,
        )

        self.assertIn("app/main.py", batch.contents)
        self.assertIn("README.md", batch.contents)
        self.assertNotIn("docs/old.md", batch.contents)
        self.assertEqual(batch.results["docs/old.md"].status, FileContentStatus.DELETED)

    def test_empty_file_retrieved_successfully(self) -> None:
        """A valid empty text file returns content='' with SUCCESS, not None."""
        changed_files = [
            ChangedFile(path="app/__init__.py", status="added", additions=0, deletions=0, changes=0)
        ]
        self.mock_client.get_file_content.return_value = ""

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        res = batch.results["app/__init__.py"]
        self.assertEqual(res.status, FileContentStatus.SUCCESS)
        self.assertEqual(res.content, "")
        self.assertTrue(res.is_success)
        self.assertEqual(res.size_bytes, 0)
        # Empty text files must be included in batch.contents with value ""
        self.assertIn("app/__init__.py", batch.contents)
        self.assertEqual(batch.contents["app/__init__.py"], "")
        self.assertTrue("app/__init__.py" in batch)
        self.assertEqual(batch["app/__init__.py"], "")
        self.assertEqual(batch.get("app/__init__.py"), "")
        self.assertEqual(len(batch.failures), 0)

    def test_renamed_file_retrieved_using_new_path(self) -> None:
        """Renamed files are retrieved using their new path at the PR head commit."""
        changed_files = [
            ChangedFile(
                path="app/services/new_auth.py",
                status="renamed",
                additions=2,
                deletions=1,
                changes=3,
            )
        ]
        self.mock_client.get_file_content.return_value = "new_auth_content"

        batch = self.service.retrieve_contents(
            repository=self.repository,
            head_commit_sha=self.head_sha,
            changed_files=changed_files,
        )

        self.mock_client.get_file_content.assert_called_once_with(
            "SurajD45", "ai-pr-investigator-demo", "app/services/new_auth.py", ref=self.head_sha
        )
        self.assertEqual(batch.contents["app/services/new_auth.py"], "new_auth_content")
        self.assertEqual(batch.results["app/services/new_auth.py"].status, FileContentStatus.SUCCESS)



class TestGitHubClientGetFileContent(unittest.TestCase):
    """Unit tests for GitHubClient.get_file_content method."""

    def setUp(self) -> None:
        self.client = GitHubClient("ghs_testToken123456")
        self.owner = "SurajD45"
        self.repo = "devsense"
        self.path = "app/main.py"
        self.ref = "6dcb09b5b57875f334f61aebed695e2e4193db5e"

    @patch("app.integrations.github.client.requests.get")
    def test_successful_base64_decoding(self, mock_get: MagicMock) -> None:
        raw_text = "print('Hello DevSense')\n"
        encoded = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "size": len(raw_text),
            "name": "main.py",
            "path": "app/main.py",
            "content": encoded,
            "sha": "aabbcc112233",
        }
        mock_get.return_value = mock_resp

        result = self.client.get_file_content(self.owner, self.repo, self.path, ref=self.ref)

        self.assertEqual(result, raw_text)
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertIn("contents/app/main.py", args[0])
        self.assertEqual(kwargs["params"], {"ref": self.ref})

    @patch("app.integrations.github.client.requests.get")
    def test_multiline_base64_decoding(self, mock_get: MagicMock) -> None:
        """GitHub API formats base64 content with newlines every 60 chars."""
        raw_text = "line 1\nline 2\nline 3\n" * 10
        b64_raw = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")
        # Split into 60-char chunks joined by \n
        chunked = "\n".join(b64_raw[i : i + 60] for i in range(0, len(b64_raw), 60))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "size": len(raw_text),
            "name": "main.py",
            "path": "app/main.py",
            "content": chunked,
            "sha": "aabbcc112233",
        }
        mock_get.return_value = mock_resp

        result = self.client.get_file_content(self.owner, self.repo, self.path, ref=self.ref)
        self.assertEqual(result, raw_text)

    @patch("app.integrations.github.client.requests.get")
    def test_directory_returns_error(self, mock_get: MagicMock) -> None:
        """If path points to a directory, API returns a list, which should raise GitHubAPIError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"name": "file1.py", "type": "file"},
            {"name": "file2.py", "type": "file"},
        ]
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAPIError) as ctx:
            self.client.get_file_content(self.owner, self.repo, "app", ref=self.ref)

        self.assertIn("directory", str(ctx.exception).lower())

    @patch("app.integrations.github.client.requests.get")
    def test_404_raises_github_not_found_error(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubNotFoundError):
            self.client.get_file_content(self.owner, self.repo, "missing.py", ref=self.ref)

    def test_validation_rejects_empty_inputs(self) -> None:
        with self.assertRaises(ValueError):
            self.client.get_file_content("", self.repo, self.path, ref=self.ref)

        with self.assertRaises(ValueError):
            self.client.get_file_content(self.owner, "", self.path, ref=self.ref)

        with self.assertRaises(ValueError):
            self.client.get_file_content(self.owner, self.repo, "", ref=self.ref)

        with self.assertRaises(ValueError):
            self.client.get_file_content(self.owner, self.repo, self.path, ref="")


    @patch("app.integrations.github.client.requests.get")
    def test_nested_and_special_character_path_encoding(self, mock_get: MagicMock) -> None:
        """Verify nested paths preserve '/' while encoding spaces and special characters."""
        raw_text = "nested code"
        encoded = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": encoded,
        }
        mock_get.return_value = mock_resp

        special_path = "app/deeply/nested/my file #1.py"
        self.client.get_file_content(self.owner, self.repo, special_path, ref=self.ref)

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertIn("contents/app/deeply/nested/my%20file%20%231.py", called_url)

    @patch("app.integrations.github.client.requests.get")
    def test_malformed_base64_raises_github_api_error(self, mock_get: MagicMock) -> None:
        """Malformed or unpadded base64 raises GitHubAPIError without exposing content."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": "%%%NOT-VALID-BASE64%%%",
        }
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAPIError) as ctx:
            self.client.get_file_content(self.owner, self.repo, self.path, ref=self.ref)

        err_msg = str(ctx.exception)
        self.assertIn("Base64", err_msg)
        self.assertIn("app/main.py", err_msg)
        # Content itself must NOT be leaked in error message
        self.assertNotIn("%%%NOT-VALID-BASE64%%%", err_msg)


if __name__ == "__main__":
    unittest.main()
