"""
DevSense — Jira Issue Key Extractor
=====================================
Extracts a Jira issue key from a GitHub Pull Request's title or body.

Responsibilities:
  - Pattern-match a Jira issue key using the canonical format [A-Z][A-Z0-9_]*-\\d+.
  - Search the PR title first; fall back to the PR body only if the title has no match.
  - Return the FIRST deterministic match when multiple keys appear in the same field.
  - Perform NO network requests and make NO calls to Jira.
  - Return None when no valid key is found (not an exception — a missing key is normal).

Determinism guarantee:
  When multiple valid Jira keys appear in the same text field, the function always
  returns the first one found by the regex engine scanning left-to-right.  This
  behavior is documented here so that callers are not surprised and do not silently
  invent a relationship between a PR and an unintended Jira issue.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Canonical Jira issue-key pattern
# ---------------------------------------------------------------------------
# Format: one uppercase letter, followed by zero or more uppercase letters,
# digits, or underscores, a hyphen, and one or more digits.
# Examples: SCRUM-5, DEV-123, PROJ_ABC-42, A-1
# ---------------------------------------------------------------------------
_JIRA_KEY_PATTERN: re.Pattern[str] = re.compile(r"[A-Z][A-Z0-9_]*-\d+")


def extract_jira_key(title: str, body: Optional[str] = None) -> Optional[str]:
    """
    Return the first Jira issue key found in the PR title, or, if the title
    contains no key, the first key found in the PR body.

    Search order
    ------------
    1. PR title  — scanned left-to-right; first match is returned immediately.
    2. PR body   — only inspected when the title yields no match.

    Parameters
    ----------
    title : str
        The pull request title.  Must be a non-empty string.
    body : str | None
        The pull request description / body.  May be None or empty.

    Returns
    -------
    str | None
        The first Jira issue key found (e.g. ``"SCRUM-5"``), or ``None`` if no
        key exists in either field.

    Notes
    -----
    * Multiple keys in the same field: the **first** match (leftmost) is
      returned.  This is intentional — callers that need all keys should use
      ``extract_all_jira_keys`` instead.
    * This function is pure and deterministic.  It performs no I/O.
    """
    if not isinstance(title, str):
        raise TypeError(f"title must be a str, got {type(title).__name__!r}")

    # --- Search PR title first ---
    title_match = _JIRA_KEY_PATTERN.search(title)
    if title_match:
        return title_match.group(0)

    # --- Fall back to PR body ---
    if body:
        body_match = _JIRA_KEY_PATTERN.search(body)
        if body_match:
            return body_match.group(0)

    return None


def extract_all_jira_keys(text: str) -> list[str]:
    """
    Return all Jira issue keys found in *text*, in left-to-right order.

    This is a helper for callers that need to discover every key mentioned
    in a field rather than just the first.  The bridge itself uses only
    ``extract_jira_key``.

    Parameters
    ----------
    text : str
        Arbitrary text to search.

    Returns
    -------
    list[str]
        Ordered list of all matching Jira keys (may be empty).
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__!r}")
    return _JIRA_KEY_PATTERN.findall(text)
