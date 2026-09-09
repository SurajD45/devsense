"""
DevSense — Unit Tests for GitHubClient
=======================================
Verifies GitHub REST API communication, error handling, parameter validation,
and PR metadata transformation without making real network calls.
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from app.integrations.github.client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)


class TestGitHubClient(unittest.TestCase):
    """Test suite for GitHubClient implementation."""

    def setUp(self) -> None:
        self.access_token = "ghs_mockInstallationToken1234567890"
        self.client = GitHubClient(self.access_token)
        self.owner = "SurajD45"
        self.repo = "ai-pr-investigator-demo"
        self.pull_number = 1

        self.mock_raw_pr_response = {
            "id": 999999,
            "node_id": "PR_kwDOJmock",
            "number": 1,
            "state": "open",
            "locked": False,
            "title": "Add initial PR investigation framework",
            "user": {
                "login": "octocat",
                "id": 1,
                "type": "User",
            },
            "body": "This PR introduces core trace verification evidence.",
            "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-01T11:00:00Z",
            "closed_at": None,
            "merged_at": None,
            "html_url": "https://github.com/SurajD45/ai-pr-investigator-demo/pull/1",
            "diff_url": "https://github.com/SurajD45/ai-pr-investigator-demo/pull/1.diff",
            "patch_url": "https://github.com/SurajD45/ai-pr-investigator-demo/pull/1.patch",
            "head": {
                "label": "SurajD45:feature-trace",
                "ref": "feature-trace",
                "sha": "6dcb09b5b57875f334f61aebed695e2e4193db5e",
            },
            "base": {
                "label": "SurajD45:main",
                "ref": "main",
                "sha": "7dcb09b5b57875f334f61aebed695e2e4193db5f",
            },
        }

    # -----------------------------------------------------------------------
    # Requirement 1: Successful PR retrieval
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_successful_pr_retrieval(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        pr = self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertEqual(pr["number"], 1)
        self.assertEqual(pr["title"], "Add initial PR investigation framework")
        self.assertEqual(pr["body"], "This PR introduces core trace verification evidence.")
        self.assertEqual(pr["state"], "open")
        self.assertEqual(pr["author"], "octocat")
        self.assertEqual(pr["html_url"], "https://github.com/SurajD45/ai-pr-investigator-demo/pull/1")
        self.assertEqual(pr["base_branch"], "main")
        self.assertEqual(pr["head_branch"], "feature-trace")
        self.assertEqual(pr["created_at"], "2026-09-01T10:00:00Z")
        self.assertEqual(pr["updated_at"], "2026-09-01T11:00:00Z")

    # -----------------------------------------------------------------------
    # Requirement 2: 404 response
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_404_not_found_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubNotFoundError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", str(ctx.exception).lower())
        self.assertTrue(issubclass(GitHubNotFoundError, GitHubAPIError))
        self.assertTrue(issubclass(GitHubNotFoundError, GitHubClientError))

    # -----------------------------------------------------------------------
    # Requirement 3: 401 response
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_401_unauthorized_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAuthenticationError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("authentication failed", str(ctx.exception).lower())
        self.assertNotIn(self.access_token, str(ctx.exception))

    # -----------------------------------------------------------------------
    # Requirement 4: 403 response
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_403_forbidden_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubPermissionError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("permission denied", str(ctx.exception).lower())
        self.assertNotIn(self.access_token, str(ctx.exception))

    # -----------------------------------------------------------------------
    # Requirement 5: Other HTTP error
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_other_http_error_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAPIError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertNotIn(self.access_token, str(ctx.exception))

    # -----------------------------------------------------------------------
    # Requirement 6: Network/request failure
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_network_connection_failure(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with self.assertRaises(GitHubNetworkError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertTrue(issubclass(GitHubNetworkError, GitHubClientError))
        self.assertNotIn(self.access_token, str(ctx.exception))

    @patch("app.integrations.github.client.requests.get")
    def test_network_timeout_failure(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.Timeout("Read timed out")

        with self.assertRaises(GitHubNetworkError) as ctx:
            self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertIn("timed out", str(ctx.exception).lower())
        self.assertNotIn(self.access_token, str(ctx.exception))

    # -----------------------------------------------------------------------
    # Requirement 7: Correct GitHub endpoint is requested
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_correct_endpoint_requested(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        self.client.get_pull_request("test-org", "test-repo", 42)

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        expected_url = "https://api.github.com/repos/test-org/test-repo/pulls/42"
        self.assertEqual(called_url, expected_url)

    # -----------------------------------------------------------------------
    # Header Requirements: Authorization, X-GitHub-Api-Version, Accept
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_authorization_header_remains_correct(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], f"Bearer {self.access_token}")

    @patch("app.integrations.github.client.requests.get")
    def test_github_api_version_header_remains_correct(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertIn("X-GitHub-Api-Version", headers)
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")

    @patch("app.integrations.github.client.requests.get")
    def test_accept_header_remains_correct(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertIn("Accept", headers)
        self.assertEqual(headers["Accept"], "application/vnd.github+json")

    # -----------------------------------------------------------------------
    # Requirement 10: Returned data contains only the intended PR metadata
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_returned_data_contains_only_intended_metadata(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        pr = self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        expected_keys = {
            "number",
            "title",
            "body",
            "state",
            "author",
            "html_url",
            "base_branch",
            "head_branch",
            "created_at",
            "updated_at",
        }
        self.assertEqual(set(pr.keys()), expected_keys)

        # Confirm non-curated raw keys are stripped
        self.assertNotIn("diff_url", pr)
        self.assertNotIn("patch_url", pr)
        self.assertNotIn("node_id", pr)
        self.assertNotIn("id", pr)

    # -----------------------------------------------------------------------
    # Requirement 11: Access token is never included in returned PR data
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_access_token_never_included_in_returned_data(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        pr = self.client.get_pull_request(self.owner, self.repo, self.pull_number)

        self.assertNotIn("access_token", pr)
        self.assertNotIn("token", pr)
        self.assertNotIn(self.access_token, str(pr))

    # -----------------------------------------------------------------------
    # Additional edge cases: Null body handling & parameter validations
    # -----------------------------------------------------------------------
    @patch("app.integrations.github.client.requests.get")
    def test_null_body_handled_safely(self, mock_get: MagicMock) -> None:
        raw_payload = dict(self.mock_raw_pr_response)
        raw_payload["body"] = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = raw_payload
        mock_get.return_value = mock_resp

        pr = self.client.get_pull_request(self.owner, self.repo, self.pull_number)
        self.assertEqual(pr["body"], "")

    def test_invalid_client_initialization(self) -> None:
        with self.assertRaises(ValueError):
            GitHubClient("")
        with self.assertRaises(ValueError):
            GitHubClient("   ")

    def test_invalid_timeout_is_rejected(self) -> None:
        invalid_timeouts = [0, -1, -5.5, "30", None, False, True, [30]]
        for timeout in invalid_timeouts:
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    GitHubClient(self.access_token, timeout=timeout)

    @patch("app.integrations.github.client.requests.get")
    def test_valid_timeout_is_accepted(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_raw_pr_response
        mock_get.return_value = mock_resp

        # Integer timeout
        client_int = GitHubClient(self.access_token, timeout=15)
        self.assertEqual(client_int._timeout, 15)
        client_int.get_pull_request(self.owner, self.repo, self.pull_number)
        self.assertEqual(mock_get.call_args[1]["timeout"], 15)

        # Float timeout
        client_float = GitHubClient(self.access_token, timeout=2.5)
        self.assertEqual(client_float._timeout, 2.5)
        client_float.get_pull_request(self.owner, self.repo, self.pull_number)
        self.assertEqual(mock_get.call_args[1]["timeout"], 2.5)

        # Default timeout
        client_default = GitHubClient(self.access_token)
        self.assertEqual(client_default._timeout, 30)

    def test_invalid_get_pull_request_parameters(self) -> None:
        with self.assertRaises(ValueError):
            self.client.get_pull_request("", self.repo, self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request(self.owner, "", self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request(self.owner, self.repo, 0)
        with self.assertRaises(ValueError):
            self.client.get_pull_request(self.owner, self.repo, -5)


class TestGitHubClientGetPullRequestFiles(unittest.TestCase):
    """Test suite for get_pull_request_files method."""

    def setUp(self) -> None:
        self.access_token = "ghs_mockInstallationToken1234567890"
        self.client = GitHubClient(self.access_token)
        self.owner = "SurajD45"
        self.repo = "ai-pr-investigator-demo"
        self.pull_number = 1

        self.mock_file_1 = {
            "sha": "bbcd538c8e72b8c175046e27cc8f907076331401",
            "filename": "app/main.py",
            "status": "modified",
            "additions": 15,
            "deletions": 5,
            "changes": 20,
            "blob_url": "https://github.com/SurajD45/demo/blob/main/app/main.py",
            "raw_url": "https://github.com/SurajD45/demo/raw/main/app/main.py",
            "contents_url": "https://api.github.com/repos/SurajD45/demo/contents/app/main.py",
            "patch": "@@ -1,5 +1,15 @@\n+new line",
        }

        self.mock_file_2 = {
            "sha": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
            "filename": "assets/logo.png",
            "status": "added",
            "additions": 0,
            "deletions": 0,
            "changes": 0,
            "blob_url": "https://github.com/SurajD45/demo/blob/main/assets/logo.png",
            "raw_url": "https://github.com/SurajD45/demo/raw/main/assets/logo.png",
            "contents_url": "https://api.github.com/repos/SurajD45/demo/contents/assets/logo.png",
            # Binary file: no patch field provided by GitHub
        }

    # 1. Single-page successful response
    @patch("app.integrations.github.client.requests.get")
    def test_single_page_successful_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [self.mock_file_1]
        mock_resp.links = {}
        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["filename"], "app/main.py")
        self.assertEqual(files[0]["status"], "modified")
        self.assertEqual(files[0]["additions"], 15)
        self.assertEqual(files[0]["deletions"], 5)
        self.assertEqual(files[0]["changes"], 20)
        self.assertEqual(files[0]["patch"], "@@ -1,5 +1,15 @@\n+new line")

    # 2. Multiple-page response
    @patch("app.integrations.github.client.requests.get")
    def test_multiple_page_response(self, mock_get: MagicMock) -> None:
        # Page 1: exactly per_page (100) items → triggers next page fetch
        page1_items = [dict(self.mock_file_1)] * 100
        resp_page1 = MagicMock()
        resp_page1.status_code = 200
        resp_page1.json.return_value = page1_items

        # Page 2: fewer than per_page → terminates pagination
        resp_page2 = MagicMock()
        resp_page2.status_code = 200
        resp_page2.json.return_value = [self.mock_file_2]

        mock_get.side_effect = [resp_page1, resp_page2]

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(len(files), 101)
        self.assertEqual(files[0]["filename"], "app/main.py")
        self.assertEqual(files[100]["filename"], "assets/logo.png")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[0][1]["params"]["page"], 1)
        self.assertEqual(mock_get.call_args_list[1][1]["params"]["page"], 2)

    # 3. Correct endpoint
    @patch("app.integrations.github.client.requests.get")
    def test_correct_endpoint_requested(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_get.return_value = mock_resp

        self.client.get_pull_request_files("owner-org", "test-repo", 10)

        called_url = mock_get.call_args[0][0]
        expected_url = "https://api.github.com/repos/owner-org/test-repo/pulls/10/files"
        self.assertEqual(called_url, expected_url)

    # 4. Correct Authorization header
    @patch("app.integrations.github.client.requests.get")
    def test_correct_authorization_header(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_get.return_value = mock_resp

        self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], f"Bearer {self.access_token}")

    # 5. Correct GitHub API version header
    @patch("app.integrations.github.client.requests.get")
    def test_correct_api_version_header(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_get.return_value = mock_resp

        self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertEqual(headers.get("X-GitHub-Api-Version"), "2022-11-28")

    # 6. Correct Accept header
    @patch("app.integrations.github.client.requests.get")
    def test_correct_accept_header(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_get.return_value = mock_resp

        self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        headers = mock_get.call_args[1]["headers"]
        self.assertEqual(headers.get("Accept"), "application/vnd.github+json")

    # 7. 404 response
    @patch("app.integrations.github.client.requests.get")
    def test_404_not_found_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubNotFoundError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", str(ctx.exception).lower())

    # 8. 401 response
    @patch("app.integrations.github.client.requests.get")
    def test_401_unauthorized_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAuthenticationError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertNotIn(self.access_token, str(ctx.exception))

    # 9. 403 response
    @patch("app.integrations.github.client.requests.get")
    def test_403_forbidden_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubPermissionError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertNotIn(self.access_token, str(ctx.exception))

    # 10. Other HTTP error
    @patch("app.integrations.github.client.requests.get")
    def test_other_http_error_response(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_get.return_value = mock_resp

        with self.assertRaises(GitHubAPIError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("HTTP 502", str(ctx.exception))
        self.assertNotIn(self.access_token, str(ctx.exception))

    # 11. Timeout
    @patch("app.integrations.github.client.requests.get")
    def test_timeout_failure(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.Timeout("Read timeout")

        with self.assertRaises(GitHubNetworkError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertIn("timed out", str(ctx.exception).lower())
        self.assertNotIn(self.access_token, str(ctx.exception))

    # 12. Network failure
    @patch("app.integrations.github.client.requests.get")
    def test_network_failure(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.ConnectionError("Network disconnected")

        with self.assertRaises(GitHubNetworkError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertIn("network error", str(ctx.exception).lower())
        self.assertNotIn(self.access_token, str(ctx.exception))

    # 13. Patch is returned when available
    @patch("app.integrations.github.client.requests.get")
    def test_patch_returned_when_available(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [self.mock_file_1]

        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(files[0]["patch"], "@@ -1,5 +1,15 @@\n+new line")

    # 14. Missing/null patch becomes empty string
    @patch("app.integrations.github.client.requests.get")
    def test_missing_and_null_patch_becomes_empty_string(self, mock_get: MagicMock) -> None:
        file_null_patch = dict(self.mock_file_1)
        file_null_patch["patch"] = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [self.mock_file_2, file_null_patch]

        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(files[0]["patch"], "")
        self.assertEqual(files[1]["patch"], "")

    # 15. Only the agreed fields are returned
    @patch("app.integrations.github.client.requests.get")
    def test_only_agreed_fields_returned(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [self.mock_file_1]

        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        expected_fields = {"filename", "status", "additions", "deletions", "changes", "patch"}
        self.assertEqual(set(files[0].keys()), expected_fields)
        self.assertNotIn("sha", files[0])
        self.assertNotIn("blob_url", files[0])
        self.assertNotIn("raw_url", files[0])
        self.assertNotIn("contents_url", files[0])

    # 16. Pagination terminates correctly
    @patch("app.integrations.github.client.requests.get")
    def test_pagination_terminates_on_fewer_items_than_per_page(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [self.mock_file_1]  # 1 item < 100 per_page

        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(len(files), 1)
        self.assertEqual(mock_get.call_count, 1)

    @patch("app.integrations.github.client.requests.get")
    def test_pagination_terminates_on_empty_list(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_get.return_value = mock_resp

        files = self.client.get_pull_request_files(self.owner, self.repo, self.pull_number)

        self.assertEqual(len(files), 0)
        self.assertEqual(mock_get.call_count, 1)

    # 17. Input validation
    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files("", self.repo, self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files("   ", self.repo, self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, "", self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, "   ", self.pull_number)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, self.repo, 0)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, self.repo, -1)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, self.repo, 1, max_pages=0)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, self.repo, 1, max_pages=-5)
        with self.assertRaises(ValueError):
            self.client.get_pull_request_files(self.owner, self.repo, 1, max_pages="10")

    # 18. Ensure the implementation cannot enter an infinite pagination loop
    @patch("app.integrations.github.client.requests.get")
    def test_infinite_pagination_loop_protection(self, mock_get: MagicMock) -> None:
        # Every page returns exactly per_page (100) items → never terminates naturally
        infinite_resp = MagicMock()
        infinite_resp.status_code = 200
        infinite_resp.json.return_value = [self.mock_file_1] * 100
        mock_get.return_value = infinite_resp

        with self.assertRaises(GitHubAPIError) as ctx:
            self.client.get_pull_request_files(self.owner, self.repo, self.pull_number, max_pages=3)

        self.assertIn("exceeded maximum pagination limit", str(ctx.exception).lower())
        self.assertEqual(mock_get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
