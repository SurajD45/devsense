"""
DevSense — Application Settings
=================================
Centralized environment variable loading.

All environment variables are loaded from the .env file (via python-dotenv)
and exposed as module-level constants.

Security:
  - No secret values are ever logged or printed by this module.
  - The .env file must remain git-ignored.
"""

import os

from dotenv import load_dotenv

# Load .env file from the project root into os.environ.
# This is safe to call multiple times — subsequent calls are no-ops.
load_dotenv()


# ---------------------------------------------------------------------------
# GitHub App
# ---------------------------------------------------------------------------

GITHUB_APP_ID: str = os.environ.get("GITHUB_APP_ID", "").strip()
GITHUB_PRIVATE_KEY_PATH: str = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "").strip()
GITHUB_REPOSITORY_OWNER: str = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
GITHUB_REPOSITORY_NAME: str = os.environ.get("GITHUB_REPOSITORY_NAME", "").strip()


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

GITHUB_WEBHOOK_SECRET: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
