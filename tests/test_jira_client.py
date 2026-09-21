"""Unit tests for the V1 Jira connectivity foundation."""

import unittest
from unittest.mock import Mock, patch

import requests

from app.integrations.jira.client import (
    JiraAuthenticationError,
    JiraClient,
    JiraClientError,
    JiraIssueNotFoundError,
    JiraNetworkError,
)
from app.integrations.jira.parser import JiraResponseError, parse_issue


class TestJiraClient(unittest.TestCase):
    def setUp(self):
        self.client = JiraClient(
            base_url="https://example.atlassian.net",
            email="developer@example.com",
            api_token="test-token",
        )

    def _response(self, status_code, payload):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        return response

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_success(self, mock_get):
        mock_get.return_value = self._response(
            200,
            {
                "key": "ABC-123",
                "fields": {
                    "summary": "Add coupon validation",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Validate expiry."}],
                            }
                        ],
                    },
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                },
            },
        )

        issue = self.client.get_issue("ABC-123")

        self.assertEqual(issue.key, "ABC-123")
        self.assertEqual(issue.summary, "Add coupon validation")
        self.assertEqual(issue.description, "Validate expiry.")
        self.assertEqual(issue.status, "To Do")
        self.assertEqual(issue.issue_type, "Story")
        mock_get.assert_called_once()
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://example.atlassian.net/rest/api/3/issue/ABC-123",
        )
        self.assertEqual(mock_get.call_args.kwargs["auth"].username, "developer@example.com")
        self.assertEqual(mock_get.call_args.kwargs["auth"].password, "test-token")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_authentication_failure(self, mock_get):
        mock_get.return_value = self._response(401, {"errorMessages": ["Unauthorized"]})
        with self.assertRaises(JiraAuthenticationError):
            self.client.get_issue("ABC-123")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_forbidden(self, mock_get):
        mock_get.return_value = self._response(403, {"errorMessages": ["Forbidden"]})
        with self.assertRaises(JiraAuthenticationError):
            self.client.get_issue("ABC-123")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_not_found(self, mock_get):
        mock_get.return_value = self._response(404, {"errorMessages": ["Issue does not exist"]})
        with self.assertRaises(JiraIssueNotFoundError):
            self.client.get_issue("ABC-999")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_network_failure(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("connection failed")
        with self.assertRaises(JiraNetworkError):
            self.client.get_issue("ABC-123")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_invalid_json(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.side_effect = ValueError("invalid json")
        mock_get.return_value = response

        with self.assertRaises(JiraResponseError):
            self.client.get_issue("ABC-123")

    @patch("app.integrations.jira.client.requests.get")
    def test_get_issue_unexpected_response(self, mock_get):
        mock_get.return_value = self._response(200, {"key": "ABC-123", "fields": {}})
        with self.assertRaises(JiraClientError):
            self.client.get_issue("ABC-123")

    def test_missing_configuration_is_rejected(self):
        with self.assertRaisesRegex(JiraClientError, "JIRA_API_TOKEN"):
            JiraClient(
                base_url="https://example.atlassian.net",
                email="developer@example.com",
                api_token="",
            )

    def test_empty_issue_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.get_issue("   ")


class TestJiraParser(unittest.TestCase):
    def test_parse_string_description(self):
        issue = parse_issue(
            {
                "key": "ABC-1",
                "fields": {
                    "summary": "Simple issue",
                    "description": "Plain text",
                },
            }
        )
        self.assertEqual(issue.description, "Plain text")
        self.assertIsNone(issue.status)
        self.assertIsNone(issue.issue_type)

    def test_parse_requires_expected_shape(self):
        with self.assertRaises(JiraResponseError):
            parse_issue({"key": "ABC-1"})


if __name__ == "__main__":
    unittest.main()
