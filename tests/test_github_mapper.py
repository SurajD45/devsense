"""Unit tests for GitHubPullRequestMapper."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.domain.models import PullRequest
from app.integrations.github.mapper import GitHubPullRequestMapper


class TestGitHubPullRequestMapper(unittest.TestCase):

    def setUp(self):
        self.mapper = GitHubPullRequestMapper()
        self.context = {
            "pull_request": {
                "number": 12,
                "title": "Add coupon validation",
                "body": "Validate coupon expiry and discount limits.",
                "author": "octocat",
                "head_branch": "feature/coupon",
                "base_branch": "main",
                "state": "open",
            },
            "files": [
                {
                    "filename": "app/coupons.py",
                    "status": "modified",
                    "additions": 8,
                    "deletions": 2,
                    "changes": 10,
                    "patch": "@@ -1,2 +1,8 @@",
                }
            ],
            "commits": [
                {
                    "sha": "abcdef1234567",
                    "message": "Add coupon validation",
                    "author": "octocat",
                    "author_email": "octocat@example.com",
                    "committer": "octocat",
                    "committer_email": "octocat@example.com",
                    "timestamp": "2026-10-01T10:00:00Z",
                }
            ],
        }

    def test_maps_pull_request_files_and_commits(self):
        result = self.mapper.to_pull_request(
            self.context, "SurajD45", "ai-pr-investigator-demo"
        )

        self.assertIsInstance(result, PullRequest)
        self.assertEqual(result.repository, "SurajD45/ai-pr-investigator-demo")
        self.assertEqual(result.number, 12)
        self.assertEqual(result.title, "Add coupon validation")
        self.assertEqual(
            result.description,
            "Validate coupon expiry and discount limits.",
        )
        self.assertEqual(result.source_branch, "feature/coupon")
        self.assertEqual(result.target_branch, "main")
        self.assertEqual(result.state, "open")

        self.assertEqual(len(result.changed_files), 1)
        self.assertEqual(result.changed_files[0].path, "app/coupons.py")
        self.assertEqual(result.changed_files[0].additions, 8)
        self.assertEqual(result.changed_files[0].deletions, 2)
        self.assertEqual(result.changed_files[0].patch, "@@ -1,2 +1,8 @@")

        self.assertEqual(len(result.commits), 1)
        self.assertEqual(result.commits[0].sha, "abcdef1234567")
        self.assertEqual(result.commits[0].author, "octocat")

    def test_maps_empty_file_and_commit_lists(self):
        self.context["files"] = []
        self.context["commits"] = []

        result = self.mapper.to_pull_request(
            self.context, "SurajD45", "ai-pr-investigator-demo"
        )

        self.assertEqual(result.changed_files, [])
        self.assertEqual(result.commits, [])

    def test_rejects_non_dictionary_context(self):
        with self.assertRaises(ValueError):
            self.mapper.to_pull_request(
                None, "SurajD45", "ai-pr-investigator-demo"
            )

    def test_rejects_missing_pull_request_data(self):
        with self.assertRaises(ValueError):
            self.mapper.to_pull_request(
                {"files": [], "commits": []},
                "SurajD45",
                "ai-pr-investigator-demo",
            )

    def test_rejects_non_list_files(self):
        self.context["files"] = {}

        with self.assertRaises(ValueError):
            self.mapper.to_pull_request(
                self.context, "SurajD45", "ai-pr-investigator-demo"
            )

    def test_domain_validation_rejects_invalid_commit_sha(self):
        self.context["commits"][0]["sha"] = "not-a-valid-sha"

        with self.assertRaises(ValidationError):
            self.mapper.to_pull_request(
                self.context, "SurajD45", "ai-pr-investigator-demo"
            )

    def test_rejects_empty_repository_owner(self):
        with self.assertRaises(ValueError):
            self.mapper.to_pull_request(self.context, " ", "my-repo")

    @patch("app.integrations.github.client.requests.get")
    def test_mapping_does_not_make_network_calls(self, mock_get):
        self.mapper.to_pull_request(
            self.context, "SurajD45", "ai-pr-investigator-demo"
        )

        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
