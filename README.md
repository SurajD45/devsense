# AI PR Investigator — Backend

Minimal Python backend for the **AI PR Investigator** GitHub App.

Currently includes:
1. **GitHub App authentication test** — one-shot script proving JWT-based auth works.
2. **Webhook receiver** — FastAPI server that receives and verifies GitHub webhook deliveries.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.9 or later |
| GitHub App | Already created with read-only permissions |
| Private Key | `.pem` file downloaded from the GitHub App settings |
| Installation | App installed on the target repository |

---

## Setup

### 1. Clone and enter the project

```bash
cd ai-pr-investigator-backend
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Activate — Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Activate — macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Place your private key

Copy your GitHub App `.pem` file into the project root (or any local path).
The file is **git-ignored** by default.

### 5. Create your `.env` file

```bash
copy .env.example .env
```

Edit `.env` and fill in your values:

```env
GITHUB_APP_ID=4791929
GITHUB_PRIVATE_KEY_PATH=./your-app-private-key.pem
GITHUB_REPOSITORY_OWNER=your-github-username
GITHUB_REPOSITORY_NAME=ai-pr-investigator-demo
GITHUB_WEBHOOK_SECRET=your-webhook-secret
```

> **Warning:** Never commit the `.env` file or `.pem` file. Both are excluded
> by `.gitignore`.

---

## 1. Run the Authentication Test

```bash
python -m scripts.github_app_test
```

### Expected output

```
==================================================
  AI PR Investigator — GitHub App Auth Test
==================================================
[✓] JWT generated successfully
[✓] Installation found (Installation ID: XXXXX)
[✓] Installation access token obtained
[✓] Repository access verified

    Repository : owner/ai-pr-investigator-demo
    Owner      : owner
    Visibility : private
    Branch     : main

==================================================
  ALL CHECKS PASSED
==================================================
```

---

## 2. Run the Webhook Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health check |
| `POST` | `/webhooks/github` | GitHub webhook receiver |

### How signature verification works

1. GitHub computes `HMAC-SHA256(webhook_secret, raw_body)` and sends it as `X-Hub-Signature-256: sha256=<hex>`.
2. Our server recomputes the same HMAC using the shared secret from `GITHUB_WEBHOOK_SECRET`.
3. The two digests are compared using `hmac.compare_digest()` (constant-time) to prevent timing attacks.
4. Requests with missing or invalid signatures receive `401 Unauthorized`.

### Local testing with curl

Generate a test signature and send a mock webhook:

```bash
# PowerShell — generate HMAC signature for a test payload
$secret = "your-webhook-secret"
$body = '{"action":"opened","pull_request":{"number":1,"html_url":"https://github.com/owner/repo/pull/1","user":{"login":"dev"},"head":{"ref":"feature"},"base":{"ref":"main"}},"repository":{"full_name":"owner/repo"}}'

$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = [System.Text.Encoding]::UTF8.GetBytes($secret)
$sig = [BitConverter]::ToString($hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($body))).Replace("-","").ToLower()

curl -X POST http://localhost:8000/webhooks/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: pull_request" `
  -H "X-GitHub-Delivery: test-123" `
  -H "X-Hub-Signature-256: sha256=$sig" `
  -d $body
```

---

## Project Structure

```
ai-pr-investigator-backend/
├── app/                          # Application package
│   ├── __init__.py
│   ├── main.py                   # FastAPI server (webhook endpoint)
│   ├── settings.py               # Centralized environment configuration
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── github/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py           # GitHub App auth (JWT, installation, token)
│   │   │   └── webhook.py        # Webhook signature verification & PR extraction
│   │   └── jira/
│   │       └── __init__.py       # Placeholder for Jira integration
│   └── domain/
│       └── __init__.py           # Placeholder for shared domain models
├── scripts/
│   └── github_app_test.py        # One-shot auth connectivity test
├── tests/
│   └── __init__.py
├── .env.example                  # Template — commit this
├── .gitignore                    # Excludes .env, *.pem, .venv/, __pycache__/
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

---

## Security

- Private key loaded from a local file; **never** printed or logged.
- Webhook secret loaded from environment; **never** logged.
- Tokens and signatures are used **in-memory only**; never printed.
- Signature comparison uses constant-time `hmac.compare_digest()`.
- `.env` and `*.pem` are git-ignored.
- All GitHub App permissions are **read-only**.

