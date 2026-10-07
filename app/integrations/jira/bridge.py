"""
DevSense — GitHub → Jira Bridge
=================================
Orchestrates the flow from a GitHub Pull Request to its linked Jira Requirement.

Flow
----
    GitHub PR (title + body)
        ↓  key_extractor.extract_jira_key()
    Jira issue key (or None)
        ↓  JiraClient.get_issue()
    JiraIssue (internal Jira model)
        ↓  JiraRequirementMapper.to_requirement()
    Requirement (canonical DevSense domain model)

Responsibilities
----------------
This module is the ONLY place that combines the three concerns above.

Each collaborator keeps its own responsibility:
  - JiraClient      — Jira API communication only.
  - JiraRequirementMapper — JiraIssue → Requirement conversion only.
  - extract_jira_key — pure text extraction, no I/O.

This bridge coordinates them without adding business logic of its own.

Security
--------
  - Credentials are never included in exception messages or log output.
  - JiraClient exceptions are allowed to propagate unchanged so that callers
    get meaningful error information without credential leakage.

Usage
-----
    from app.integrations.jira.bridge import GitHubJiraBridge, BridgeResult

    bridge = GitHubJiraBridge(jira_client, mapper, jira_base_url="https://acme.atlassian.net")
    result = bridge.get_requirement_for_pr(pr_title="SCRUM-5 Add login", pr_body=None)

    if result.requirement is not None:
        print(result.requirement.requirement_id)   # "SCRUM-5"
    else:
        print(result.reason)  # "No Jira key found in PR title or body."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.domain.models import Requirement
from app.integrations.jira.client import JiraClient
from app.integrations.jira.key_extractor import extract_jira_key
from app.integrations.jira.mapper import JiraRequirementMapper


# ---------------------------------------------------------------------------
# Bridge Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BridgeResult:
    """
    The outcome of one GitHub PR → Jira Requirement lookup.

    Attributes
    ----------
    requirement : Requirement | None
        The canonical DevSense Requirement if a linked Jira issue was found
        and successfully mapped.  ``None`` when no key exists in the PR.
    jira_key : str | None
        The extracted Jira issue key (e.g. ``"SCRUM-5"``), or ``None`` if the
        PR did not reference any Jira issue.
    reason : str
        A human-readable explanation of the outcome.  Always set, never empty.
        Examples:
          - "No Jira key found in PR title or body."
          - "Requirement mapped from Jira issue SCRUM-5."
    """

    requirement: Optional[Requirement]
    jira_key: Optional[str]
    reason: str

    # Convenience predicates
    @property
    def has_requirement(self) -> bool:
        """True when a Requirement was successfully resolved."""
        return self.requirement is not None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class GitHubJiraBridge:
    """
    Orchestrates the GitHub PR → Jira Requirement lookup flow.

    Parameters
    ----------
    jira_client : JiraClient
        Configured Jira HTTP client.  The bridge never constructs one itself
        so that callers control configuration and the client can be easily
        swapped for a mock in tests.
    mapper : JiraRequirementMapper
        Maps a JiraIssue to a canonical Requirement domain model.
    jira_base_url : str
        Base URL of the Jira instance (e.g. ``"https://acme.atlassian.net"``).
        Required by the mapper to build the ``source`` URL on the Requirement.
    """

    def __init__(
        self,
        jira_client: JiraClient,
        mapper: JiraRequirementMapper,
        jira_base_url: str,
    ) -> None:
        if not isinstance(jira_base_url, str) or not jira_base_url.strip():
            raise ValueError("jira_base_url must be a non-empty string")
        self._jira_client = jira_client
        self._mapper = mapper
        self._jira_base_url = jira_base_url.strip()

    def get_requirement_for_pr(
        self,
        pr_title: str,
        pr_body: Optional[str] = None,
    ) -> BridgeResult:
        """
        Resolve the Jira Requirement linked to a GitHub Pull Request.

        Parameters
        ----------
        pr_title : str
            The pull request title.  Searched first for a Jira key.
        pr_body : str | None
            The pull request description / body.  Searched only when the title
            contains no Jira key.

        Returns
        -------
        BridgeResult
            Always returns a ``BridgeResult``.  When no key is present the
            ``requirement`` field is ``None`` and ``reason`` explains why.
            When a key is present but Jira raises an exception, the exception
            propagates to the caller unchanged.

        Raises
        ------
        JiraClientError (and subclasses)
            Any Jira API error (network, auth, not-found, etc.) propagates
            unchanged.  Credentials are never included in exception messages.
        TypeError
            If ``pr_title`` is not a string.
        """
        # --- Step 1: Extract Jira key ---
        jira_key = extract_jira_key(pr_title, pr_body)

        if jira_key is None:
            return BridgeResult(
                requirement=None,
                jira_key=None,
                reason="No Jira key found in PR title or body.",
            )

        # --- Step 2: Fetch Jira issue (errors propagate) ---
        jira_issue = self._jira_client.get_issue(jira_key)

        # --- Step 3: Map to canonical Requirement ---
        requirement = self._mapper.to_requirement(jira_issue, self._jira_base_url)

        return BridgeResult(
            requirement=requirement,
            jira_key=jira_key,
            reason=f"Requirement mapped from Jira issue {jira_key}.",
        )
