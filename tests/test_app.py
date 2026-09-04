"""
DevSense — Unit & Functional Tests for FastAPI Application & Webhook Handler
"""

import hashlib
import hmac
import json
import unittest

import asyncio
from starlette.requests import Request

from app.integrations.github.webhook import extract_pr_info, verify_signature
from app.main import github_webhook, health_check
from app.settings import GITHUB_WEBHOOK_SECRET


class TestWebhookVerification(unittest.TestCase):
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

    def test_extract_pr_info_handled_action(self):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/org/repo/pull/42",
                "user": {"login": "testuser"},
                "head": {"ref": "feature-branch"},
                "base": {"ref": "main"},
            },
            "repository": {
                "full_name": "org/repo",
            },
        }
        info = extract_pr_info(payload)
        self.assertIsNotNone(info)
        self.assertEqual(info["action"], "opened")
        self.assertEqual(info["repo_full_name"], "org/repo")
        self.assertEqual(info["pr_number"], 42)
        self.assertEqual(info["pr_author"], "testuser")
        self.assertEqual(info["source_branch"], "feature-branch")
        self.assertEqual(info["target_branch"], "main")

    def test_extract_pr_info_unhandled_action(self):
        payload = {"action": "closed"}
        self.assertIsNone(extract_pr_info(payload))


class TestFastAPIEndpoints(unittest.TestCase):
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

    def test_webhook_pull_request_opened(self):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 101,
                "html_url": "https://github.com/owner/repo/pull/101",
                "user": {"login": "octocat"},
                "head": {"ref": "patch-1"},
                "base": {"ref": "main"},
            },
            "repository": {
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
        response = asyncio.run(github_webhook(req))
        self.assertEqual(response["status"], "processed")
        self.assertEqual(response["pr_number"], 101)
        self.assertEqual(response["action"], "opened")


if __name__ == "__main__":
    unittest.main()
