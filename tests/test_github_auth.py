"""
DevSense — Unit & Functional Tests for GitHub App Auth & Service Factory
========================================================================
Covers:
- create_jwt()
- get_installation_id()
- create_installation_token()
- verify_repository_access()
- get_installation_client()
"""

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import requests

from app.integrations.github.auth import (
    GITHUB_API_BASE,
    GITHUB_API_VERSION,
    JWT_ALGORITHM,
    JWT_CLOCK_DRIFT_SECONDS,
    JWT_EXPIRY_SECONDS,
    create_jwt,
    create_installation_token,
    get_installation_id,
    verify_repository_access,
)
from app.integrations.github.client import GitHubClient
from app.integrations.github.service import get_installation_client


class BaseAuthTestCase(unittest.TestCase):
    """Base setup that generates an ephemeral RSA key pair for testing."""

    @classmethod
    def setUpClass(cls):
        # Generate genuine 2048-bit RSA key for testing RS256 signing
        cls._rsa_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.private_pem = cls._rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.public_pem = cls._rsa_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        # Write to temporary file
        cls._temp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
        cls._temp_file.write(cls.private_pem)
        cls._temp_file.close()
        cls.key_path = cls._temp_file.name

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.key_path):
            try:
                os.remove(cls.key_path)
            except OSError:
                pass


class TestCreateJWT(BaseAuthTestCase):
    """Test suite for create_jwt()."""

    def setUp(self):
        self.app_id = "123456"

    def test_jwt_generated_successfully(self):
        token = create_jwt(self.app_id, self.key_path)
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 50)

    def test_jwt_algorithm_is_rs256(self):
        token = create_jwt(self.app_id, self.key_path)
        header = jwt.get_unverified_header(token)
        self.assertEqual(header.get("alg"), JWT_ALGORITHM)
        self.assertEqual(header.get("typ"), "JWT")

    def test_jwt_payload_has_correct_app_id(self):
        token = create_jwt(self.app_id, self.key_path)
        payload = jwt.decode(token, self.public_pem, algorithms=["RS256"])
        self.assertEqual(payload.get("iss"), self.app_id)

    def test_jwt_iat_and_exp_are_present(self):
        token = create_jwt(self.app_id, self.key_path)
        payload = jwt.decode(token, self.public_pem, algorithms=["RS256"])
        self.assertIn("iat", payload)
        self.assertIn("exp", payload)

    def test_jwt_expiry_window_is_reasonable(self):
        before = int(time.time())
        token = create_jwt(self.app_id, self.key_path)
        after = int(time.time())

        payload = jwt.decode(token, self.public_pem, algorithms=["RS256"])
        iat = payload["iat"]
        exp = payload["exp"]

        # iat is set with drift buffer (now - 60)
        self.assertTrue(before - JWT_CLOCK_DRIFT_SECONDS <= iat <= after)
        # exp is set to now + 540
        self.assertTrue(before <= exp <= after + JWT_EXPIRY_SECONDS + 5)
        # Total validity window: 540 + 60 = 600 seconds (10 minutes)
        self.assertEqual(exp - iat, JWT_EXPIRY_SECONDS + JWT_CLOCK_DRIFT_SECONDS)

    def test_nonexistent_private_key_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            create_jwt(self.app_id, "nonexistent_key_path_12345.pem")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_empty_private_key_file_raises_value_error(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as empty_file:
            empty_file.write("   \n")
            empty_path = empty_file.name
        try:
            with self.assertRaises(ValueError) as ctx:
                create_jwt(self.app_id, empty_path)
            self.assertIn("empty", str(ctx.exception).lower())
        finally:
            if os.path.exists(empty_path):
                os.remove(empty_path)

    def test_invalid_app_id_raises_value_error(self):
        for invalid_id in ["", "   ", None]:
            with self.assertRaises(ValueError):
                create_jwt(invalid_id, self.key_path)

    def test_invalid_private_key_path_raises_value_error(self):
        for invalid_path in ["", "   ", None]:
            with self.assertRaises(ValueError):
                create_jwt(self.app_id, invalid_path)


class TestGetInstallationId(unittest.TestCase):
    """Test suite for get_installation_id()."""

    def setUp(self):
        self.jwt_token = "fake-jwt-token"
        self.owner = "test-owner"

    @patch("requests.get")
    def test_successful_installation_lookup(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 111, "account": {"login": "other-org"}},
            {"id": 999, "account": {"login": "test-owner"}},
        ]
        mock_get.return_value = mock_response

        installation_id = get_installation_id(self.jwt_token, self.owner)
        self.assertEqual(installation_id, 999)

        # Verify correct URL and headers
        mock_get.assert_called_once_with(
            f"{GITHUB_API_BASE}/app/installations",
            headers={
                "Authorization": f"Bearer {self.jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            timeout=30,
        )

    @patch("requests.get")
    def test_installation_lookup_case_insensitive(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 456, "account": {"login": "Test-Owner"}},
        ]
        mock_get.return_value = mock_response

        installation_id = get_installation_id(self.jwt_token, "test-owner")
        self.assertEqual(installation_id, 456)

    @patch("requests.get")
    def test_installation_not_found_raises_runtime_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 111, "account": {"login": "unrelated-org"}},
        ]
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError) as ctx:
            get_installation_id(self.jwt_token, "nonexistent-owner")
        self.assertIn("No installation found", str(ctx.exception))

    @patch("requests.get")
    def test_http_failure_propagates(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            get_installation_id(self.jwt_token, self.owner)

    @patch("requests.get")
    def test_malformed_response_format_raises_value_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "unexpected dict instead of list"}
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError) as ctx:
            get_installation_id(self.jwt_token, self.owner)
        self.assertIn("expected list", str(ctx.exception).lower())

    def test_input_validation(self):
        for invalid_token in ["", "   ", None]:
            with self.assertRaises(ValueError):
                get_installation_id(invalid_token, "owner")

        for invalid_owner in ["", "   ", None]:
            with self.assertRaises(ValueError):
                get_installation_id("token", invalid_owner)


class TestCreateInstallationToken(unittest.TestCase):
    """Test suite for create_installation_token()."""

    def setUp(self):
        self.jwt_token = "fake-jwt-token"
        self.installation_id = 12345

    @patch("requests.post")
    def test_successful_token_creation(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "token": "ghs_installation_access_token_abc123",
            "expires_at": "2026-09-11T21:00:00Z",
        }
        mock_post.return_value = mock_response

        token = create_installation_token(self.jwt_token, self.installation_id)
        self.assertEqual(token, "ghs_installation_access_token_abc123")

        mock_post.assert_called_once_with(
            f"{GITHUB_API_BASE}/app/installations/{self.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            timeout=30,
        )

    @patch("requests.post")
    def test_http_failure_propagates(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_post.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            create_installation_token(self.jwt_token, self.installation_id)

    @patch("requests.post")
    def test_missing_token_in_response_raises_value_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"expires_at": "2026-09-11T21:00:00Z"}
        mock_post.return_value = mock_response

        with self.assertRaises(ValueError) as ctx:
            create_installation_token(self.jwt_token, self.installation_id)
        self.assertIn("did not contain an access token", str(ctx.exception))

    def test_input_validation(self):
        for invalid_jwt in ["", "  ", None]:
            with self.assertRaises(ValueError):
                create_installation_token(invalid_jwt, self.installation_id)

        for invalid_id in [0, -1, -100, "123", None, True, False]:
            with self.assertRaises(ValueError):
                create_installation_token(self.jwt_token, invalid_id)


class TestVerifyRepositoryAccess(unittest.TestCase):
    """Test suite for verify_repository_access()."""

    def setUp(self):
        self.token = "fake-token"
        self.owner = "test-owner"
        self.repo = "test-repo"

    @patch("requests.get")
    def test_successful_access_returns_sanitized_data(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "full_name": "test-owner/test-repo",
            "owner": {"login": "test-owner"},
            "private": True,
            "default_branch": "main",
            "node_id": "secret-node-id",
            "git_url": "git://github.com/test-owner/test-repo.git",
        }
        mock_get.return_value = mock_response

        repo_info = verify_repository_access(self.token, self.owner, self.repo)
        self.assertEqual(repo_info, {
            "full_name": "test-owner/test-repo",
            "owner": "test-owner",
            "visibility": "private",
            "default_branch": "main",
        })

        mock_get.assert_called_once_with(
            f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            timeout=30,
        )

    @patch("requests.get")
    def test_public_repo_visibility(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "full_name": "test-owner/test-repo",
            "owner": {"login": "test-owner"},
            "private": False,
            "default_branch": "main",
        }
        mock_get.return_value = mock_response

        repo_info = verify_repository_access(self.token, self.owner, self.repo)
        self.assertEqual(repo_info["visibility"], "public")

    @patch("requests.get")
    def test_401_unauthorized_propagates(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            verify_repository_access(self.token, self.owner, self.repo)

    @patch("requests.get")
    def test_403_forbidden_propagates(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("403 Forbidden")
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            verify_repository_access(self.token, self.owner, self.repo)

    @patch("requests.get")
    def test_404_not_found_propagates(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            verify_repository_access(self.token, self.owner, self.repo)

    @patch("requests.get")
    def test_network_timeout_propagates(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

        with self.assertRaises(requests.exceptions.Timeout):
            verify_repository_access(self.token, self.owner, self.repo)

    def test_input_validation(self):
        for invalid_token in ["", "  ", None]:
            with self.assertRaises(ValueError):
                verify_repository_access(invalid_token, self.owner, self.repo)

        for invalid_owner in ["", "  ", None]:
            with self.assertRaises(ValueError):
                verify_repository_access(self.token, invalid_owner, self.repo)

        for invalid_repo in ["", "  ", None]:
            with self.assertRaises(ValueError):
                verify_repository_access(self.token, self.owner, invalid_repo)


class TestGetInstallationClient(unittest.TestCase):
    """Test suite for get_installation_client() factory."""

    def setUp(self):
        self.installation_id = 998877
        self.fake_jwt = "fake-app-jwt"
        self.fake_token = "ghs_test_installation_token_xyz"
        self.fake_app_id = "12345"
        self.fake_key_path = "/path/to/key.pem"

    @patch("app.integrations.github.service.create_installation_token")
    @patch("app.integrations.github.service.create_jwt")
    def test_successful_client_creation(self, mock_jwt, mock_token):
        mock_jwt.return_value = self.fake_jwt
        mock_token.return_value = self.fake_token

        client = get_installation_client(
            self.installation_id,
            app_id=self.fake_app_id,
            private_key_path=self.fake_key_path,
        )

        self.assertIsInstance(client, GitHubClient)
        mock_jwt.assert_called_once_with(self.fake_app_id, self.fake_key_path)
        mock_token.assert_called_once_with(self.fake_jwt, self.installation_id)

    @patch("app.integrations.github.service.create_installation_token")
    @patch("app.integrations.github.service.create_jwt")
    def test_custom_parameters_forwarded(self, mock_jwt, mock_token):
        mock_jwt.return_value = self.fake_jwt
        mock_token.return_value = self.fake_token

        client = get_installation_client(
            self.installation_id,
            app_id=self.fake_app_id,
            private_key_path=self.fake_key_path,
            base_url="https://github-enterprise.acme.com/api/v3",
            timeout=15,
        )

        self.assertEqual(client._base_url, "https://github-enterprise.acme.com/api/v3")
        self.assertEqual(client._timeout, 15)

    def test_invalid_installation_id_raises_value_error(self):
        for invalid_id in [0, -1, -50, "123", None, True, False]:
            with self.assertRaises(ValueError) as ctx:
                get_installation_client(invalid_id)
            self.assertIn("installation_id must be a positive integer", str(ctx.exception))

    def test_missing_app_id_raises_value_error(self):
        with patch("app.integrations.github.service.GITHUB_APP_ID", ""):
            with self.assertRaises(ValueError) as ctx:
                get_installation_client(self.installation_id, app_id="")
            self.assertIn("GITHUB_APP_ID", str(ctx.exception))

    def test_missing_private_key_path_raises_value_error(self):
        with patch("app.integrations.github.service.GITHUB_PRIVATE_KEY_PATH", ""):
            with self.assertRaises(ValueError) as ctx:
                get_installation_client(
                    self.installation_id,
                    app_id=self.fake_app_id,
                    private_key_path="",
                )
            self.assertIn("GITHUB_PRIVATE_KEY_PATH", str(ctx.exception))

    @patch("app.integrations.github.service.create_jwt")
    def test_jwt_creation_error_propagates(self, mock_jwt):
        mock_jwt.side_effect = FileNotFoundError("Private key file not found")

        with self.assertRaises(FileNotFoundError):
            get_installation_client(
                self.installation_id,
                app_id=self.fake_app_id,
                private_key_path=self.fake_key_path,
            )

    @patch("app.integrations.github.service.create_installation_token")
    @patch("app.integrations.github.service.create_jwt")
    def test_installation_token_error_propagates(self, mock_jwt, mock_token):
        mock_jwt.return_value = self.fake_jwt
        mock_token.side_effect = requests.exceptions.HTTPError("403 Forbidden")

        with self.assertRaises(requests.exceptions.HTTPError):
            get_installation_client(
                self.installation_id,
                app_id=self.fake_app_id,
                private_key_path=self.fake_key_path,
            )

    @patch("app.integrations.github.service.create_installation_token")
    @patch("app.integrations.github.service.create_jwt")
    def test_token_not_exposed_in_public_representation(self, mock_jwt, mock_token):
        mock_jwt.return_value = self.fake_jwt
        mock_token.return_value = self.fake_token

        client = get_installation_client(
            self.installation_id,
            app_id=self.fake_app_id,
            private_key_path=self.fake_key_path,
        )

        client_repr = repr(client)
        client_str = str(client)
        self.assertNotIn(self.fake_token, client_repr)
        self.assertNotIn(self.fake_token, client_str)
        self.assertNotIn(self.fake_jwt, client_repr)
        self.assertNotIn(self.fake_jwt, client_str)


if __name__ == "__main__":
    unittest.main()
