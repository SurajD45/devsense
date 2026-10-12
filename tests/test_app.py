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
from app.domain.models import (
    AcceptanceCriterion,
    InvestigationStatus,
    PullRequest,
    Requirement,
)
from app.integrations.github.content import (
    ChangedFileContentBatch,
    FileContentResult,
    FileContentStatus,
)
from app.integrations.github.webhook import extract_pr_info, verify_signature
from app.integrations.jira.bridge import BridgeResult
from app.integrations.jira.client import JiraAuthenticationError, JiraNetworkError
from app.investigation.pipeline import InvestigationPipelineOrchestrator
from app.investigation.schemas import (
    InvestigationPipelineInput,
    InvestigationResultBundle,
)
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
        self.assertIsNone(info["head_commit_sha"])

    def test_extract_pr_info_preserves_head_commit_sha(self):
        payload = dict(self.valid_payload)
        payload["pull_request"] = dict(self.valid_payload["pull_request"])
        payload["pull_request"]["head"] = {
            "ref": "feature-branch",
            "sha": "0123456789abcdef0123456789abcdef01234567",
        }
        info = extract_pr_info(payload)
        self.assertIsNotNone(info)
        self.assertEqual(info["head_commit_sha"], "0123456789abcdef0123456789abcdef01234567")

    def test_extract_pr_info_normalizes_uppercase_sha(self):
        payload = dict(self.valid_payload)
        payload["pull_request"] = dict(self.valid_payload["pull_request"])
        payload["pull_request"]["head"] = {
            "ref": "feature-branch",
            "sha": "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
        }
        info = extract_pr_info(payload)
        self.assertIsNotNone(info)
        self.assertEqual(info["head_commit_sha"], "abcdef0123456789abcdef0123456789abcdef01")

    def test_extract_pr_info_missing_or_malformed_head_sha_is_none(self):
        for bad_sha in ["short", "123456", "not-hex!", None, "", "   "]:
            payload = dict(self.valid_payload)
            payload["pull_request"] = dict(self.valid_payload["pull_request"])
            payload["pull_request"]["head"] = {
                "ref": "feature-branch",
                "sha": bad_sha,
            }
            info = extract_pr_info(payload)
            self.assertIsNotNone(info)
            self.assertIsNone(info["head_commit_sha"])


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

        # Raw-payload equality and JSON serialization compatibility
        self.assertEqual(result, self.valid_github_context)
        self.assertEqual(result.raw_context, self.valid_github_context)
        self.assertEqual(result["pull_request"]["number"], 10)
        self.assertEqual(result["pull_request"]["title"], "SCRUM-5 Implement user authentication")

        # Verify JSON serialization succeeds without TypeError and matches raw context
        serialized = json.dumps(result)
        expected_json = json.dumps(self.valid_github_context)
        self.assertEqual(serialized, expected_json)
        self.assertEqual(json.dumps(result.raw_context), expected_json)

        # Verify domain models are NOT present as dictionary keys
        self.assertNotIn("pull_request_model", result)
        self.assertNotIn("bridge_result", result)

    @patch("app.main.get_installation_client")
    def test_jira_configuration_missing_with_no_jira_key(self, mock_get_github_client):
        """Fix 2: When Jira config is missing and PR has no Jira key, no error is raised."""
        no_key_context = dict(self.valid_github_context)
        no_key_context["pull_request"] = dict(self.valid_github_context["pull_request"])
        no_key_context["pull_request"]["title"] = "chore: update dependencies and clean code"
        no_key_context["pull_request"]["body"] = "Routine maintenance with no ticket."

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = no_key_context
        mock_get_github_client.return_value = mock_github

        # With JIRA_BASE_URL="", JiraClient() would raise JiraConfigurationError if called.
        with patch("app.main.JIRA_BASE_URL", ""):
            result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        self.assertIsInstance(result.pull_request, PullRequest)
        self.assertIsNotNone(result.bridge_result)
        self.assertFalse(result.bridge_result.has_requirement)
        self.assertIsNone(result.bridge_result.jira_key)
        self.assertEqual(result.bridge_result.reason, "No Jira key found in PR title or body.")
        # Raw dictionary equality and JSON serialization work
        self.assertEqual(result, no_key_context)
        self.assertEqual(json.dumps(result), json.dumps(no_key_context))


class TestPhase3HeadShaAndContentRetrieval(unittest.TestCase):
    """Test suite for Phase 3 Step 1: Head SHA provenance & content retrieval integration."""

    def setUp(self):
        self.pr_info = {
            "action": "opened",
            "installation_id": 998877,
            "owner": "SurajD45",
            "repo": "devsense",
            "pull_number": 10,
            "head_commit_sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        }
        self.raw_github_context = {
            "pull_request": {
                "number": 10,
                "title": "SCRUM-5 Implement user authentication",
                "body": "Fixes SCRUM-5 by implementing JWT authentication.",
                "author": "suraj",
                "head_branch": "feature/auth",
                "base_branch": "main",
                "state": "open",
                "head_sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
            },
            "files": [
                {
                    "filename": "app/auth.py",
                    "status": "modified",
                    "additions": 42,
                    "deletions": 0,
                    "changes": 42,
                    "patch": "@@ -0,0 +1,42 @@",
                }
            ],
            "commits": [
                {
                    "sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
                    "message": "feat: add user authentication",
                    "author": "suraj",
                }
            ],
        }

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_retrieves_file_contents_pinned_to_head_sha(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()
        mock_batch = MagicMock()
        mock_batch.contents = {"app/auth.py": "def authenticate(): pass"}
        mock_batch.failures = {}
        mock_content_service.retrieve_contents.return_value = mock_batch

        result = process_pull_request_context(
            self.pr_info,
            content_service=mock_content_service,
        )

        self.assertIsNotNone(result)
        self.assertEqual(
            result.head_commit_sha,
            "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        )
        self.assertEqual(result.file_contents, {"app/auth.py": "def authenticate(): pass"})
        self.assertIs(result.content_batch, mock_batch)

        mock_content_service.retrieve_contents.assert_called_once_with(
            repository="SurajD45/devsense",
            head_commit_sha="a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
            changed_files=result.pull_request.changed_files,
            client=mock_github,
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_resolves_head_sha_from_github_context_when_missing_in_pr_info(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        info_without_sha = dict(self.pr_info)
        del info_without_sha["head_commit_sha"]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()
        mock_batch = MagicMock()
        mock_batch.contents = {"app/auth.py": "content"}
        mock_batch.failures = {}
        mock_content_service.retrieve_contents.return_value = mock_batch

        result = process_pull_request_context(
            info_without_sha,
            content_service=mock_content_service,
        )

        self.assertEqual(
            result.head_commit_sha,
            "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        )
        mock_content_service.retrieve_contents.assert_called_once_with(
            repository="SurajD45/devsense",
            head_commit_sha="a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
            changed_files=result.pull_request.changed_files,
            client=mock_github,
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_prefers_live_api_sha_when_differing_and_logs_discrepancy(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        webhook_info = dict(self.pr_info)
        webhook_info["head_commit_sha"] = "1111111111111111111111111111111111111111"

        live_context = dict(self.raw_github_context)
        live_context["pull_request"] = dict(self.raw_github_context["pull_request"])
        live_context["pull_request"]["head_sha"] = "2222222222222222222222222222222222222222"

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = live_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()
        mock_batch = MagicMock()
        mock_batch.contents = {"app/auth.py": "content"}
        mock_batch.failures = {}
        mock_content_service.retrieve_contents.return_value = mock_batch

        with self.assertLogs("server", level="WARNING") as log_cm:
            result = process_pull_request_context(
                webhook_info,
                content_service=mock_content_service,
            )

        self.assertEqual(
            result.head_commit_sha,
            "2222222222222222222222222222222222222222",
        )
        mock_content_service.retrieve_contents.assert_called_once_with(
            repository="SurajD45/devsense",
            head_commit_sha="2222222222222222222222222222222222222222",
            changed_files=result.pull_request.changed_files,
            client=mock_github,
        )

        # Verify discrepancy diagnostic log records repository, PR number, short prefixes, and selection
        discrepancy_log = [
            msg for msg in log_cm.output if "PR head discrepancy" in msg
        ]
        self.assertTrue(discrepancy_log, "Discrepancy log should be emitted")
        self.assertIn("SurajD45/devsense PR #10", discrepancy_log[0])
        self.assertIn("1111111", discrepancy_log[0])
        self.assertIn("2222222", discrepancy_log[0])
        self.assertIn("selected live GitHub head", discrepancy_log[0])

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_skips_retrieval_when_live_sha_missing_even_with_valid_webhook_sha(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        webhook_info = dict(self.pr_info)
        webhook_info["head_commit_sha"] = "3333333333333333333333333333333333333333"

        live_context = dict(self.raw_github_context)
        live_context["pull_request"] = dict(self.raw_github_context["pull_request"])
        del live_context["pull_request"]["head_sha"]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = live_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()

        with self.assertLogs("server", level="WARNING") as log_cm:
            result = process_pull_request_context(
                webhook_info,
                content_service=mock_content_service,
            )

        self.assertIsNone(result.head_commit_sha)
        self.assertEqual(result.file_contents, {})
        self.assertIsNone(result.content_batch)
        mock_content_service.retrieve_contents.assert_not_called()

        warning_log = [
            msg for msg in log_cm.output if "Live GitHub PR head SHA is unavailable" in msg
        ]
        self.assertTrue(warning_log, "Warning log should be emitted")
        self.assertIn("SurajD45/devsense PR #10", warning_log[0])
        self.assertIn("3333333", warning_log[0])
        self.assertIn("guarantee snapshot consistency", warning_log[0])

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_skips_retrieval_when_live_sha_malformed_even_with_valid_webhook_sha(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        webhook_info = dict(self.pr_info)
        webhook_info["head_commit_sha"] = "4444444444444444444444444444444444444444"

        live_context = dict(self.raw_github_context)
        live_context["pull_request"] = dict(self.raw_github_context["pull_request"])
        live_context["pull_request"]["head_sha"] = "not-a-valid-hex-sha"

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = live_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()

        result = process_pull_request_context(
            webhook_info,
            content_service=mock_content_service,
        )

        self.assertIsNone(result.head_commit_sha)
        self.assertEqual(result.file_contents, {})
        self.assertIsNone(result.content_batch)
        mock_content_service.retrieve_contents.assert_not_called()

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_skips_retrieval_when_neither_source_provides_valid_sha(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        webhook_info = dict(self.pr_info)
        webhook_info["head_commit_sha"] = "malformed-webhook-sha"

        live_context = dict(self.raw_github_context)
        live_context["pull_request"] = dict(self.raw_github_context["pull_request"])
        live_context["pull_request"]["head_sha"] = "malformed-live-sha"

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = live_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()

        result = process_pull_request_context(
            webhook_info,
            content_service=mock_content_service,
        )

        self.assertIsNone(result.head_commit_sha)
        self.assertEqual(result.file_contents, {})
        self.assertIsNone(result.content_batch)
        mock_content_service.retrieve_contents.assert_not_called()

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_fails_closed_when_head_sha_missing(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        info_without_sha = dict(self.pr_info)
        del info_without_sha["head_commit_sha"]

        context_without_sha = dict(self.raw_github_context)
        context_without_sha["pull_request"] = dict(self.raw_github_context["pull_request"])
        del context_without_sha["pull_request"]["head_sha"]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_without_sha
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()

        result = process_pull_request_context(
            info_without_sha,
            content_service=mock_content_service,
        )

        self.assertIsNone(result.head_commit_sha)
        self.assertEqual(result.file_contents, {})
        self.assertIsNone(result.content_batch)
        mock_content_service.retrieve_contents.assert_not_called()

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_fails_closed_when_head_sha_malformed(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        info_bad_sha = dict(self.pr_info)
        info_bad_sha["head_commit_sha"] = "not-a-valid-sha"

        context_without_sha = dict(self.raw_github_context)
        context_without_sha["pull_request"] = dict(self.raw_github_context["pull_request"])
        del context_without_sha["pull_request"]["head_sha"]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_without_sha
        mock_get_github_client.return_value = mock_github

        mock_content_service = MagicMock()

        result = process_pull_request_context(
            info_bad_sha,
            content_service=mock_content_service,
        )

        self.assertIsNone(result.head_commit_sha)
        self.assertEqual(result.file_contents, {})
        self.assertIsNone(result.content_batch)
        mock_content_service.retrieve_contents.assert_not_called()

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_process_pull_request_context_handles_deleted_binary_and_failed_files(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        context_mixed_files = dict(self.raw_github_context)
        context_mixed_files["files"] = [
            {"filename": "app/valid.py", "status": "modified"},
            {"filename": "old/deleted.py", "status": "removed"},
            {"filename": "assets/logo.png", "status": "added"},
        ]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_mixed_files
        mock_github.get_file_content.return_value = "print('valid code')"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertEqual(result.file_contents, {"app/valid.py": "print('valid code')"})
        self.assertNotIn("old/deleted.py", result.file_contents)
        self.assertNotIn("assets/logo.png", result.file_contents)
        self.assertEqual(
            result.content_batch.results["old/deleted.py"].status.value,
            "DELETED",
        )
        self.assertEqual(
            result.content_batch.results["assets/logo.png"].status.value,
            "BINARY",
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_processed_pr_context_preserves_dict_equality_and_json_serialization(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_github.get_file_content.return_value = "def auth(): pass"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        # Dictionary payload equality with raw context
        self.assertEqual(result, self.raw_github_context)
        self.assertEqual(result.raw_context, self.raw_github_context)

        # JSON serialization matches raw context JSON
        self.assertEqual(json.dumps(result), json.dumps(self.raw_github_context))

        # Attributes exist and are not dictionary keys
        self.assertIn("app/auth.py", result.file_contents)
        self.assertEqual(
            result.head_commit_sha,
            "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        )
        self.assertNotIn("head_commit_sha", result)
        self.assertNotIn("file_contents", result)
        self.assertNotIn("content_batch", result)
        self.assertNotIn("investigation_bundle", result)
        self.assertNotIn("investigation_error", result)


class TestWebhookInvestigationPipelineOrchestration(unittest.TestCase):
    """
    Test suite for connecting the webhook background worker to InvestigationPipelineOrchestrator
    (DevSense Phase 3, Step 2).
    """

    def setUp(self):
        self.pr_info = {
            "installation_id": 123456,
            "owner": "SurajD45",
            "repo": "devsense",
            "pull_number": 10,
            "head_commit_sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        }
        self.raw_github_context = {
            "pull_request": {
                "number": 10,
                "title": "SCRUM-5 Implement user authentication",
                "body": "Fixes SCRUM-5 by implementing JWT authentication.",
                "author": "suraj",
                "head_branch": "feature/auth",
                "base_branch": "main",
                "state": "open",
                "head_sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
            },
            "files": [
                {
                    "filename": "app/auth.py",
                    "status": "modified",
                    "additions": 42,
                    "deletions": 0,
                    "changes": 42,
                    "patch": "@@ -0,0 +1,42 @@",
                }
            ],
            "commits": [
                {
                    "sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
                    "message": "feat: add user authentication",
                    "author": "suraj",
                }
            ],
        }
        self.mock_requirement = Requirement(
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
        self.valid_bridge_result = BridgeResult(
            requirement=self.mock_requirement,
            jira_key="SCRUM-5",
            reason="Requirement mapped from Jira issue SCRUM-5.",
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_successful_worker_to_orchestrator_invocation(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """1. Successful worker-to-orchestrator invocation with exact input and live SHA."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_content_service = MagicMock()
        mock_batch = MagicMock()
        mock_batch.contents = {"app/auth.py": "def authenticate(): pass\n"}
        mock_batch.failures = {}
        mock_content_service.retrieve_contents.return_value = mock_batch

        mock_orchestrator = MagicMock(spec=InvestigationPipelineOrchestrator)
        mock_bundle = MagicMock(spec=InvestigationResultBundle)
        mock_bundle.investigation = MagicMock(status=InvestigationStatus.COMPLETED)
        mock_bundle.findings = [MagicMock()]
        mock_bundle.evidence = [MagicMock()]
        mock_bundle.errors = []
        mock_orchestrator.orchestrate.return_value = mock_bundle

        result = process_pull_request_context(
            self.pr_info,
            content_service=mock_content_service,
            orchestrator=mock_orchestrator,
        )

        self.assertIsNotNone(result)
        self.assertIs(result.investigation_bundle, mock_bundle)
        self.assertIsNone(result.investigation_error)
        self.assertEqual(
            result.head_commit_sha,
            "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        )

        mock_orchestrator.orchestrate.assert_called_once()
        call_args, call_kwargs = mock_orchestrator.orchestrate.call_args
        pipeline_input = call_args[0]
        self.assertIsInstance(pipeline_input, InvestigationPipelineInput)
        self.assertEqual(pipeline_input.pull_request.number, 10)
        self.assertEqual(pipeline_input.requirement.requirement_id, "SCRUM-5")
        self.assertEqual(
            pipeline_input.file_contents,
            {"app/auth.py": "def authenticate(): pass\n"},
        )
        self.assertEqual(pipeline_input.test_results, [])
        self.assertEqual(
            call_kwargs.get("head_commit_sha"),
            "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4",
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_deterministic_e2e_orchestration_without_mock_orchestrator(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """1b. Real deterministic InvestigationPipelineOrchestrator runs end-to-end."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_github.get_file_content.return_value = "def login(): pass\n"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.investigation_bundle)
        self.assertEqual(
            result.investigation_bundle.investigation.status,
            InvestigationStatus.COMPLETED,
        )
        self.assertEqual(len(result.investigation_bundle.findings), 1)
        self.assertEqual(
            result.investigation_bundle.findings[0].criterion_id,
            "AC-1",
        )
        self.assertIsNone(result.investigation_error)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_jira_requirement_missing(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """2. Missing Jira requirement skips orchestration and sets diagnostic error."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=None,
            jira_key=None,
            reason="No Jira key found in PR title or body.",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            self.pr_info,
            orchestrator=mock_orchestrator,
        )

        mock_orchestrator.orchestrate.assert_not_called()
        self.assertIsNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn("Missing linked Jira requirement", result.investigation_error)
        self.assertIn("No Jira key found", result.investigation_error)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_jira_requirement_has_no_criteria(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """2b. Jira requirement with empty acceptance criteria skips orchestration."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_get_github_client.return_value = mock_github

        empty_req = Requirement(
            requirement_id="SCRUM-5",
            title="Empty requirement",
            source="https://jira.example.com/browse/SCRUM-5",
            status="In Progress",
            acceptance_criteria=[],
        )
        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = BridgeResult(
            requirement=empty_req,
            jira_key="SCRUM-5",
            reason="Requirement mapped with no criteria.",
        )
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            self.pr_info,
            orchestrator=mock_orchestrator,
        )

        mock_orchestrator.orchestrate.assert_not_called()
        self.assertIsNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn("contains no acceptance criteria", result.investigation_error)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_live_head_sha_missing(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """3. Missing live head SHA skips orchestration and sets diagnostic error."""
        context_no_sha = dict(self.raw_github_context)
        context_no_sha["pull_request"] = dict(self.raw_github_context["pull_request"])
        del context_no_sha["pull_request"]["head_sha"]

        info_no_sha = dict(self.pr_info)
        del info_no_sha["head_commit_sha"]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_no_sha
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            info_no_sha,
            orchestrator=mock_orchestrator,
        )

        mock_orchestrator.orchestrate.assert_not_called()
        self.assertIsNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn(
            "Authoritative live GitHub PR head SHA is unavailable",
            result.investigation_error,
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_changeset_is_empty(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """4. Empty changeset skips orchestration without crashing worker."""
        context_no_files = dict(self.raw_github_context)
        context_no_files["files"] = []

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_no_files
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            self.pr_info,
            orchestrator=mock_orchestrator,
        )

        mock_orchestrator.orchestrate.assert_not_called()
        self.assertIsNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn("contains no changed files", result.investigation_error)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestrator_exception_is_trapped_and_logged(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """5. Orchestrator exception is trapped, logged, and exposes diagnostic error."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_github.get_file_content.return_value = "def auth(): pass"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()
        mock_orchestrator.orchestrate.side_effect = RuntimeError("Parser crashed unexpectedly")

        with self.assertLogs("server", level="ERROR") as log_cm:
            result = process_pull_request_context(
                self.pr_info,
                orchestrator=mock_orchestrator,
            )

        self.assertIsNotNone(result)
        self.assertIsNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn(
            "Pipeline execution failure: RuntimeError: Parser crashed unexpectedly",
            result.investigation_error,
        )

        # Confirm error log with exc_info was emitted
        error_logs = [msg for msg in log_cm.output if "Investigation pipeline exception" in msg]
        self.assertTrue(error_logs)
        self.assertIn("SurajD45/devsense PR #10", error_logs[0])

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestrator_returning_failed_bundle_is_exposed(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """5b. Orchestrator returning FAILED bundle populates bundle and investigation_error."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_github.get_file_content.return_value = "def auth(): pass"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()
        failed_bundle = MagicMock(spec=InvestigationResultBundle)
        failed_bundle.investigation = MagicMock(status=InvestigationStatus.FAILED)
        failed_bundle.findings = []
        failed_bundle.evidence = []
        failed_bundle.errors = ["AST parse error in app/auth.py"]
        mock_orchestrator.orchestrate.return_value = failed_bundle

        result = process_pull_request_context(
            self.pr_info,
            orchestrator=mock_orchestrator,
        )

        self.assertIsNotNone(result)
        self.assertIs(result.investigation_bundle, failed_bundle)
        self.assertEqual(result.investigation_error, "AST parse error in app/auth.py")

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_partial_file_retrieval_and_visible_failure_diagnostics(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """6. Partial file retrieval runs orchestration on successful files and surfaces failure diagnostics."""
        context_two_files = dict(self.raw_github_context)
        context_two_files["files"] = [
            {"filename": "app/auth.py", "status": "modified"},
            {"filename": "app/broken.py", "status": "modified"},
        ]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_two_files
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        # Content service returns partial results: auth.py SUCCESS, broken.py ERROR
        batch = ChangedFileContentBatch()
        batch.results["app/auth.py"] = FileContentResult(
            path="app/auth.py",
            content="def auth(): pass",
            status=FileContentStatus.SUCCESS,
            size_bytes=16,
        )
        batch.results["app/broken.py"] = FileContentResult(
            path="app/broken.py",
            content=None,
            status=FileContentStatus.ERROR,
            error="GitHub API 500 server error",
        )

        mock_content_service = MagicMock()
        mock_content_service.retrieve_contents.return_value = batch

        mock_orchestrator = MagicMock(spec=InvestigationPipelineOrchestrator)
        mock_bundle = MagicMock(spec=InvestigationResultBundle)
        mock_bundle.investigation = MagicMock(status=InvestigationStatus.COMPLETED)
        mock_bundle.findings = [MagicMock()]
        mock_bundle.evidence = [MagicMock()]
        mock_bundle.errors = []
        mock_orchestrator.orchestrate.return_value = mock_bundle

        result = process_pull_request_context(
            self.pr_info,
            content_service=mock_content_service,
            orchestrator=mock_orchestrator,
        )

        self.assertIsNotNone(result)
        # Orchestration ran with only successfully retrieved files
        mock_orchestrator.orchestrate.assert_called_once()
        pipeline_input = mock_orchestrator.orchestrate.call_args[0][0]
        self.assertEqual(pipeline_input.file_contents, {"app/auth.py": "def auth(): pass"})
        self.assertNotIn("app/broken.py", pipeline_input.file_contents)

        # Batch preserves full failure information
        self.assertIn("app/broken.py", result.content_batch.failures)
        self.assertEqual(
            result.content_batch.failures["app/broken.py"],
            "GitHub API 500 server error",
        )

        # Result bundle and investigation_error surface visible diagnostics
        self.assertIsNotNone(result.investigation_bundle)
        self.assertIsNotNone(result.investigation_error)
        self.assertIn("Incomplete file content retrieval", result.investigation_error)
        self.assertIn("app/broken.py", result.investigation_error)
        self.assertIn(result.investigation_error, mock_bundle.errors)

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_partial_file_retrieval_with_real_orchestrator(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """6b. Partial file retrieval with real orchestrator updates bundle.errors cleanly."""
        context_two_files = dict(self.raw_github_context)
        context_two_files["files"] = [
            {"filename": "app/auth.py", "status": "modified"},
            {"filename": "app/broken.py", "status": "modified"},
        ]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_two_files
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        batch = ChangedFileContentBatch()
        batch.results["app/auth.py"] = FileContentResult(
            path="app/auth.py",
            content="def login(): pass\n",
            status=FileContentStatus.SUCCESS,
            size_bytes=19,
        )
        batch.results["app/broken.py"] = FileContentResult(
            path="app/broken.py",
            content=None,
            status=FileContentStatus.ERROR,
            error="GitHub API 500 server error",
        )

        mock_content_service = MagicMock()
        mock_content_service.retrieve_contents.return_value = batch

        result = process_pull_request_context(
            self.pr_info,
            content_service=mock_content_service,
        )

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.investigation_bundle)
        self.assertEqual(
            result.investigation_bundle.investigation.status,
            InvestigationStatus.COMPLETED,
        )
        self.assertIsNotNone(result.investigation_error)
        self.assertIn("Incomplete file content retrieval", result.investigation_error)
        self.assertIn("app/broken.py", result.investigation_error)
        self.assertIn(
            result.investigation_error,
            result.investigation_bundle.errors,
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_all_eligible_files_fail_retrieval(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """6c. When all eligible files fail retrieval, orchestration is skipped and failures remain accessible."""
        context_files = dict(self.raw_github_context)
        context_files["files"] = [
            {"filename": "app/auth.py", "status": "modified"},
            {"filename": "app/service.py", "status": "modified"},
        ]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_files
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        batch = ChangedFileContentBatch()
        batch.results["app/auth.py"] = FileContentResult(
            path="app/auth.py",
            content=None,
            status=FileContentStatus.ERROR,
            error="API timeout (504)",
        )
        batch.results["app/service.py"] = FileContentResult(
            path="app/service.py",
            content=None,
            status=FileContentStatus.NOT_FOUND,
            error="File not found at ref",
        )

        mock_content_service = MagicMock()
        mock_content_service.retrieve_contents.return_value = batch

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            self.pr_info,
            content_service=mock_content_service,
            orchestrator=mock_orchestrator,
        )

        # Assert orchestrator is NOT called
        mock_orchestrator.orchestrate.assert_not_called()

        # Assert bundle is None and diagnostic error is set
        self.assertIsNotNone(result)
        self.assertIsNone(result.investigation_bundle)
        self.assertEqual(
            result.investigation_error,
            "No usable changed-file content could be retrieved; orchestration skipped.",
        )

        # Assert content batch and failure metadata remain intact and accessible
        self.assertIs(result.content_batch, batch)
        self.assertEqual(result.file_contents, {})
        self.assertIn("app/auth.py", result.content_batch.failures)
        self.assertIn("app/service.py", result.content_batch.failures)
        self.assertEqual(
            result.content_batch.failures["app/auth.py"],
            "API timeout (504)",
        )
        self.assertEqual(
            result.content_batch.failures["app/service.py"],
            "File not found at ref",
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_orchestration_skipped_when_all_changed_files_are_deleted_or_binary(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """6d. When all changed files are deleted or binary, orchestration is skipped safely."""
        context_non_text = dict(self.raw_github_context)
        context_non_text["files"] = [
            {"filename": "old_code.py", "status": "removed"},
            {"filename": "assets/logo.png", "status": "added"},
        ]

        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = context_non_text
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        mock_orchestrator = MagicMock()

        result = process_pull_request_context(
            self.pr_info,
            orchestrator=mock_orchestrator,
        )

        mock_orchestrator.orchestrate.assert_not_called()
        self.assertIsNotNone(result)
        self.assertIsNone(result.investigation_bundle)
        self.assertEqual(
            result.investigation_error,
            "No usable changed-file content could be retrieved; orchestration skipped.",
        )
        self.assertEqual(result.file_contents, {})
        self.assertEqual(
            result.content_batch.results["old_code.py"].status,
            FileContentStatus.DELETED,
        )
        self.assertEqual(
            result.content_batch.results["assets/logo.png"].status,
            FileContentStatus.BINARY,
        )

    @patch("app.main.get_jira_bridge")
    @patch("app.main.get_installation_client")
    def test_processed_pr_context_dict_equality_and_json_with_attached_bundle(
        self, mock_get_github_client, mock_get_jira_bridge
    ):
        """7. ProcessedPRContext preserves dict equality and json.dumps() with attached bundle."""
        mock_github = MagicMock()
        mock_github.get_pull_request_context.return_value = self.raw_github_context
        mock_github.get_file_content.return_value = "def auth(): pass\n"
        mock_get_github_client.return_value = mock_github

        mock_bridge = MagicMock()
        mock_bridge.get_requirement_for_pr.return_value = self.valid_bridge_result
        mock_get_jira_bridge.return_value = mock_bridge

        result = process_pull_request_context(self.pr_info)

        self.assertIsNotNone(result.investigation_bundle)
        # Raw dict equality
        self.assertEqual(result, self.raw_github_context)
        self.assertEqual(result.raw_context, self.raw_github_context)

        # JSON serialization matches raw context JSON exactly
        self.assertEqual(json.dumps(result), json.dumps(self.raw_github_context))

        # Attributes exist and are not in dictionary keys
        self.assertNotIn("investigation_bundle", result)
        self.assertNotIn("investigation_error", result)
        self.assertNotIn("head_commit_sha", result)
        self.assertNotIn("file_contents", result)
        self.assertNotIn("content_batch", result)

    def test_processed_pr_context_explicit_attributes(self):
        """7b. ProcessedPRContext constructor initializes investigation attributes cleanly."""
        mock_bundle = MagicMock(spec=InvestigationResultBundle)
        ctx = ProcessedPRContext(
            raw_context={"key": "value"},
            investigation_bundle=mock_bundle,
            investigation_error="test-error",
        )

        self.assertEqual(ctx, {"key": "value"})
        self.assertEqual(ctx.raw_context, {"key": "value"})
        self.assertEqual(json.dumps(ctx), json.dumps({"key": "value"}))
        self.assertIs(ctx.investigation_bundle, mock_bundle)
        self.assertEqual(ctx.investigation_error, "test-error")
        self.assertNotIn("investigation_bundle", ctx)
        self.assertNotIn("investigation_error", ctx)

    @patch("app.main.process_pull_request_context")
    def test_webhook_endpoint_schedules_orchestration_background_task(
        self, mock_process_pr
    ):
        """8. Webhook endpoint returns HTTP 200 and schedules background task."""
        client = TestClient(app)
        payload = {
            "action": "opened",
            "installation": {"id": 123456},
            "pull_request": {
                "number": 10,
                "html_url": "https://github.com/SurajD45/devsense/pull/10",
                "user": {"login": "suraj"},
                "head": {"ref": "feature/auth", "sha": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"},
                "base": {"ref": "main"},
            },
            "repository": {
                "name": "devsense",
                "full_name": "SurajD45/devsense",
                "owner": {"login": "SurajD45"},
            },
        }
        body = json.dumps(payload).encode("utf-8")
        signature = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-789",
                "Content-Type": "application/json",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted", "pr_number": 10})
        mock_process_pr.assert_called_once()
        called_pr_info = mock_process_pr.call_args[0][0]
        self.assertEqual(called_pr_info["pull_number"], 10)
        self.assertEqual(called_pr_info["installation_id"], 123456)


if __name__ == "__main__":
    unittest.main()
