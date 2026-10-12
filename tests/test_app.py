"""
DevSense — Unit & Functional Tests for FastAPI Application, Webhook Handler & Background Processor
===================================================================================================
Covers:
- Webhook signature verification
- extract_pr_info() validation and extraction rules
- FastAPI endpoints (GET /health, POST /webhooks/github)
- Background task scheduling & error handling
- End-to-end webhook-to-context flow
"""

import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

import asyncio
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.integrations.github.client import (
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubNetworkError,
    GitHubAPIError,
)
from app.domain.models import AcceptanceCriterion, PullRequest, Requirement
from app.integrations.github.webhook import extract_pr_info, verify_signature
from app.integrations.jira.bridge import BridgeResult
from app.integrations.jira.client import JiraAuthenticationError, JiraNetworkError
from app.main import (
    ProcessedPRContext,
    app,
    github_webhook,
    health_check,
    process_pull_request_context,
)
from app.settings import GITHUB_WEBHOOK_SECRET


class TestWebhookSignatureVerification(unittest.TestCase):
    """Test suite for verify_signature()."""

    def setUp(self):
        self.secret = "test-secret-123"
        self.body = b'{"action": "opened"}'

    def test_verify_signature_valid(self):
        expected_sig = "sha256=" + hmac.new(
            self.secret.encode("utf-8"),
            self.body,
            hashlib.sha256,
        ).hexdigest()
        self.assertTrue(verify_signature(self.body, expected_sig, self.secret))

    def test_verify_signature_invalid(self):
        self.assertFalse(verify_signature(self.body, "sha256=invalid", self.secret))

    def test_verify_signature_missing_prefix(self):
        digest = hmac.new(self.secret.encode("utf-8"), self.body, hashlib.sha256).hexdigest()
        self.assertFalse(verify_signature(self.body, digest, self.secret))

    def test_verify_signature_empty_signature(self):
        self.assertFalse(verify_signature(self.body, "", self.secret))


class TestExtractPRInfo(unittest.TestCase):
    """Test suite for extract_pr_info()."""

    def setUp(self):
        self.valid_payload = {
            "action": "opened",
            "installation": {"id": 4791929},
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/org/repo/pull/42",
                "user": {"login": "testuser"},
                "head": {"ref": "feature-branch"},
                "base": {"ref": "main"},
            },
            "repository": {
                "name": "repo",
                "full_name": "org/repo",
                "owner": {"login": "org"},
            },
        }

    def test_complete_extract_pr_info(self):
        info = extract_pr_info(self.valid_payload)
        self.assertIsNotNone(info)
        self.assertEqual(info["action"], "opened")
        self.assertEqual(info["installation_id"], 4791929)
        self.assertEqual(info["owner"], "org")
        self.assertEqual(info["repo"], "repo")
        self.assertEqual(info["pull_number"], 42)
        self.assertEqual(info["repo_full_name"], "org/repo")
        self.assertEqual(info["pr_author"], "testuser")
        self.assertEqual(info["source_branch"], "feature-branch")
        self.assertEqual(info["target_branch"], "main")

    def test_handled_actions_supported(self):
        for action in ["opened", "synchronize", "reopened"]:
            payload = dict(self.valid_payload)
            payload["action"] = action
            info = extract_pr_info(payload)
            self.assertIsNotNone(info)
            self.assertEqual(info["action"], action)

    def test_unhandled_action_returns_none(self):
        for action in ["closed", "labeled", "assigned", "edited", "review_requested"]:
            payload = dict(self.valid_payload)
            payload["action"] = action
            self.assertIsNone(extract_pr_info(payload))

    def test_missing_installation_id_returns_none(self):
        payload = dict(self.valid_payload)
        del payload["installation"]
        self.assertIsNone(extract_pr_info(payload))

    def test_missing_repository_returns_none(self):
        payload = dict(self.valid_payload)
        del payload["repository"]
        self.assertIsNone(extract_pr_info(payload))

    def test_null_installation_returns_none(self):
        payload = dict(self.valid_payload)
        payload["installation"] = None
        self.assertIsNone(extract_pr_info(payload))

    def test_null_pull_request_returns_none(self):
        payload = dict(self.valid_payload)
        payload["pull_request"] = None
        self.assertIsNone(extract_pr_info(payload))

    def test_null_repository_returns_none(self):
        payload = dict(self.valid_payload)
        payload["repository"] = None
        self.assertIsNone(extract_pr_info(payload))

    def test_invalid_installation_id_returns_none(self):
        for invalid_id in [0, -1, -50, True, False, "4791929", None, []]:
            payload = dict(self.valid_payload)
            payload["installation"] = {"id": invalid_id}
            self.assertIsNone(extract_pr_info(payload))

    def test_invalid_pr_number_returns_none(self):
        for invalid_num in [0, -1, -100, True, False, "42", None, {}]:
            payload = dict(self.valid_payload)
            payload["pull_request"] = dict(self.valid_payload["pull_request"])
            payload["pull_request"]["number"] = invalid_num
            self.assertIsNone(extract_pr_info(payload))

    def test_fallback_parsing_from_full_name_when_owner_or_name_missing(self):
        payload = dict(self.valid_payload)
        payload["repository"] = {"full_name": "fallback-owner/fallback-repo"}
        info = extract_pr_info(payload)
        self.assertIsNotNone(info)
        self.assertEqual(info["owner"], "fallback-owner")
        self.assertEqual(info["repo"], "fallback-repo")

    def test_empty_owner_or_repo_returns_none(self):
        payload = dict(self.valid_payload)
        payload["repository"] = {"name": "", "owner": {"login": ""}, "full_name": ""}
        self.assertIsNone(extract_pr_info(payload))

    def test_non_dict_payload_returns_none(self):
        for invalid_payload in ["string", 123, [1, 2], None]:
            self.assertIsNone(extract_pr_info(invalid_payload))


class TestProcessPullRequestContext(unittest.TestCase):
    """Test suite for process_pull_request_context() background worker."""

    def setUp(self):
        self.pr_info = {
            "installation_id": 998877,
            "owner": "SurajD45",
            "repo": "devsense",
            "pull_number": 5,
        }
        self.mock_context = {
            "pull_request": {"number": 5, "title": "Test PR"},
            "files": [{"filename": "app/main.py", "status": "modified"}],
            "commits": [{"sha": "abc1234", "message": "feat: test"}],
        }

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_pull_request_context.return_value = self.mock_context
        mock_get_client.return_value = mock_client

        context = process_pull_request_context(self.pr_info)

        self.assertEqual(context, self.mock_context)
        mock_get_client.assert_called_once_with(998877)
        mock_client.get_pull_request_context.assert_called_once_with("SurajD45", "devsense", 5)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_auth_failure(self, mock_get_client):
        mock_get_client.side_effect = GitHubAuthenticationError("Auth failed")

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_not_found(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_pull_request_context.side_effect = GitHubNotFoundError("PR not found")
        mock_get_client.return_value = mock_client

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_permission_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_pull_request_context.side_effect = GitHubPermissionError("Rate limit / 403")
        mock_get_client.return_value = mock_client

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_network_failure(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_pull_request_context.side_effect = GitHubNetworkError("Connection timed out")
        mock_get_client.return_value = mock_client

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_pull_request_context.side_effect = GitHubAPIError("Server error 500")
        mock_get_client.return_value = mock_client

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)

    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_unexpected_exception(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("Unexpected runtime failure")

        context = process_pull_request_context(self.pr_info)
        self.assertIsNone(context)


class TestFastAPIEndpoints(unittest.TestCase):
    """Test suite for FastAPI endpoints."""

    def test_health_check(self):
        result = asyncio.run(health_check())
        self.assertEqual(result, {"status": "ok"})

    def _build_mock_request(self, body: bytes, headers: dict) -> Request:
        header_tuples = [
            (k.lower().encode("latin1"), v.encode("latin1"))
            for k, v in headers.items()
        ]
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/github",
            "headers": header_tuples,
        }
        return Request(scope, receive)

    def test_webhook_unauthorized_when_missing_signature(self):
        req = self._build_mock_request(b"{}", {})
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response.status_code, 401)

    def test_webhook_unauthorized_when_invalid_signature(self):
        req = self._build_mock_request(b"{}", {"X-Hub-Signature-256": "sha256=invalid"})
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response.status_code, 401)

    def test_webhook_ping_event(self):
        body = b'{"zen": "Keep it logically awesome."}'
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "test-delivery-ping",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response, {"status": "pong"})

    def test_webhook_unsupported_event(self):
        body = b'{"action": "created"}'
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": "test-delivery-issues",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response["status"], "ignored")
        self.assertIn("issues", response["reason"])

    def test_webhook_malformed_json_returns_400(self):
        body = b"not-valid-json"
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-malformed",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response.status_code, 400)

    def test_webhook_json_not_object_returns_400(self):
        body = b"[1, 2, 3]"
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-array",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response.status_code, 400)

    def test_webhook_unsupported_pr_action_returns_200_ignored(self):
        body = json.dumps({"action": "closed"}).encode("utf-8")
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-closed",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response["status"], "ignored")
        self.assertIn("closed", response["reason"])

    def test_webhook_missing_required_pr_fields_returns_400(self):
        # Action is handled ("opened"), but missing installation and pull_request
        body = json.dumps({"action": "opened"}).encode("utf-8")
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-incomplete",
                "Content-Type": "application/json",
            },
        )
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response.status_code, 400)

    def test_valid_webhook_schedules_background_task(self):
        payload = {
            "action": "opened",
            "installation": {"id": 123456},
            "pull_request": {
                "number": 101,
                "html_url": "https://github.com/owner/repo/pull/101",
                "user": {"login": "octocat"},
                "head": {"ref": "patch-1"},
                "base": {"ref": "main"},
            },
            "repository": {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        req = self._build_mock_request(
            body,
            {
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-pr",
                "Content-Type": "application/json",
            },
        )

        mock_background = MagicMock(spec=BackgroundTasks)
        response = asyncio.run(github_webhook(req, mock_background))

        self.assertEqual(response["status"], "accepted")
        self.assertEqual(response["pr_number"], 101)

        mock_background.add_task.assert_called_once()
        args, _ = mock_background.add_task.call_args
        self.assertEqual(args[0], process_pull_request_context)
        scheduled_pr_info = args[1]
        self.assertEqual(scheduled_pr_info["installation_id"], 123456)
        self.assertEqual(scheduled_pr_info["owner"], "owner")
        self.assertEqual(scheduled_pr_info["repo"], "repo")
        self.assertEqual(scheduled_pr_info["pull_number"], 101)


class TestEndToEndWebhookFlow(unittest.TestCase):
    """End-to-end functional test using FastAPI TestClient."""

    def setUp(self):
        self.client = TestClient(app)
        self.payload = {
            "action": "synchronize",
            "installation": {"id": 555666},
            "pull_request": {
                "number": 7,
                "html_url": "https://github.com/test-org/test-repo/pull/7",
                "user": {"login": "alice"},
                "head": {"ref": "feature/v2"},
                "base": {"ref": "main"},
            },
            "repository": {
                "name": "test-repo",
                "owner": {"login": "test-org"},
                "full_name": "test-org/test-repo",
            },
        }
        self.body = json.dumps(self.payload).encode("utf-8")
        self.sig = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            self.body,
            hashlib.sha256,
        ).hexdigest()

    @patch("app.main.get_installation_client")
    def test_complete_webhook_to_context_flow(self, mock_get_client):
        mock_github_client = MagicMock()
        mock_github_client.get_pull_request_context.return_value = {
            "pull_request": {"number": 7, "title": "Add feature"},
            "files": [{"filename": "foo.py", "status": "added", "patch": "+print(1)"}],
            "commits": [{"sha": "def5678", "message": "feat: add foo"}],
        }
        mock_get_client.return_value = mock_github_client

        response = self.client.post(
            "/webhooks/github",
            content=self.body,
            headers={
                "X-Hub-Signature-256": self.sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-e2e-delivery",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data, {"status": "accepted", "pr_number": 7})

        # Verify TestClient executed background task and called client methods
        mock_get_client.assert_called_once_with(555666)
        mock_github_client.get_pull_request_context.assert_called_once_with("test-org", "test-repo", 7)


class TestPRContextProcessingIntegration(unittest.TestCase):
    """
    Focused test suite for PR mapping and GitHub-Jira requirement bridge integration
    inside process_pull_request_context().
    """

    def setUp(self):
        self.pr_info = {
            "installation_id": 123456,
            "owner": "SurajD45",
            "repo": "devsense",
            "pull_number": 10,
        }
        self.valid_github_context = {
            "pull_request": {
                "number": 10,
                "title": "SCRUM-5 Implement user authentication",
                "body": "Fixes SCRUM-5 by implementing JWT authentication.",
                "author": "suraj",
                "head_branch": "feature/auth",
                "base_branch": "main",
                "state": "open",
            },
            "files": [
                {
                    "filename": "app/auth.py",
                    "status": "added",
                    "additions": 42,
                    "deletions": 0,
                    "changes": 42,
                    "patch": "@@ -0,0 +1,42 @@",
                }
            ],
            "commits": [
                {
                    "sha": "a1b2c3d4e5f6",
                    "message": "feat: add user authentication",
                    "author": "suraj",
                }
            ],
        }

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_successful_pr_mapping_and_jira_resolution(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """1. Successful PR mapping and Jira requirement resolution."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.valid_github_context
        mock_get_github_client.return_value = mock_github

        mock_requirement = Requirement(
            requirement_id="SCRUM-5",
            title="Implement user authentication",
            description="Users must authenticate via JWT",
            source="https://jira.example.com/browse/SCRUM-5",
            status="In Progress",
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="AC-1",
                    title="User can log in with valid credentials",
                )
            ],
        )
        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=mock_requirement,
            jira_key="SCRUM-5",
            reason="Requirement mapped from Jira issue SCRUM-5.",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        # Verify PullRequest domain model mapping
        self.assertIsInstance(result.pull_request, PullRequest)
        self.assertEqual(result.pull_request.repository, "SurajD45/devsense")
        self.assertEqual(result.pull_request.number, 10)
        self.assertEqual(result.pull_request.title, "SCRUM-5 Implement user authentication")
        self.assertEqual(result.pull_request.author, "suraj")
        self.assertEqual(result.pull_request.source_branch, "feature/auth")
        self.assertEqual(result.pull_request.target_branch, "main")
        self.assertEqual(result.pull_request.state, "open")
        self.assertEqual(len(result.pull_request.changed_files), 1)
        self.assertEqual(result.pull_request.changed_files[0].path, "app/auth.py")
        self.assertEqual(len(result.pull_request.commits), 1)
        self.assertEqual(result.pull_request.commits[0].sha, "a1b2c3d4e5f6")

        # Verify Jira bridge result
        self.assertIsNotNone(result.bridge_result)
        self.assertTrue(result.bridge_result.has_requirement)
        self.assertEqual(result.bridge_result.jira_key, "SCRUM-5")
        self.assertEqual(result.bridge_result.requirement.requirement_id, "SCRUM-5")
        self.assertEqual(len(result.bridge_result.requirement.acceptance_criteria), 1)
        self.assertEqual(
            result.bridge_result.requirement.acceptance_criteria[0].criterion_id, "AC-1"
        )
        mock_bridge.get_requirement_for_pr.assert_called_once_with(
            pr_title="SCRUM-5 Implement user authentication",
            pr_body="Fixes SCRUM-5 by implementing JWT authentication.",
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_pr_without_jira_key(self, mock_get_github_client, mock_get_jira_bridge):
        """2. A PR without a Jira key."""
        no_key_context = dict(self.valid_github_context)
        no_key_context["pull_request"] = dict(self.valid_github_context["pull_request"])
        no_key_context["pull_request"]["title"] = "Refactor logging utilities"
        no_key_context["pull_request"]["body"] = "Small cleanup with no Jira reference."

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = no_key_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key found in PR title or body.",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        self.assertIsInstance(result.pull_request, PullRequest)
        self.assertEqual(result.pull_request.number, 10)
        self.assertIsNotNone(result.bridge_result)
        self.assertFalse(result.bridge_result.has_requirement)
        self.assertIsNone(result.bridge_result.requirement)
        self.assertIsNone(result.bridge_result.jira_key)
        self.assertIn("No Jira key found", result.bridge_result.reason)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_jira_api_network_failure(self, mock_get_github_client, mock_get_jira_bridge):
        """3. Jira API/network failure must not crash background processing."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.valid_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.side_effect = JiraNetworkError("Jira host unreachable")
        mock_get_jira_bridge.return_value = mock_bridge

        # Must not raise / crash
        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        self.assertIsInstance(result.pull_request, PullRequest)
        self.assertEqual(result.pull_request.number, 10)
        # Bridge failed, so bridge_result is None
        self.assertIsNone(result.bridge_result)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_jira_authentication_failure(self, mock_get_github_client, mock_get_jira_bridge):
        """Jira authentication failure must not crash background processing."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.valid_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.side_effect = JiraAuthenticationError("401 Unauthorized")
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        self.assertIsInstance(result.pull_request, PullRequest)
        self.assertIsNone(result.bridge_result)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_correct_handling_of_mapped_pr_and_bridge_result(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """4. Correct handling of the mapped PR and bridge result."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.valid_github_context
        mock_get_github_client.return_value = mock_github

        mock_requirement = Requirement(
            requirement_id="SCRUM-5",
            title="Implement user authentication",
            source="https://jira.example.com/browse/SCRUM-5",
            status="In Progress",
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="AC-1", title="AC 1")
            ],
        )
        expected_bridge_result = BridgeResult(
            requirement=mock_requirement,
            jira_key="SCRUM-5",
            reason="Requirement mapped from Jira issue SCRUM-5.",
        )
        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = expected_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        # Attribute-level access for downstream orchestration
        self.assertIsInstance(result, ProcessedPRContext)
        self.assertIs(result.pull_request, result.pull_request)
        self.assertEqual(result.pull_request.repository, "SurajD45/devsense")
        self.assertIs(result.bridge_result, expected_bridge_result)

        # Key-level access for dictionary compatibility
        self.assertEqual(result["pull_request_model"], result.pull_request)
        self.assertEqual(result["bridge_result"], expected_bridge_result)
        self.assertEqual(result.get("pull_request_model"), result.pull_request)
        self.assertEqual(result.get("bridge_result"), expected_bridge_result)
        self.assertEqual(result.raw_context["pull_request"]["number"], 10)


if __name__ == "__main__":
    unittest.main()
