"""DevSense — JiraIssue to Requirement Domain Mapper."""

from __future__ import annotations

import re

from app.domain.models import AcceptanceCriterion, Requirement
from app.integrations.jira.models import JiraIssue

_EXPLICIT_AC_PATTERN = re.compile(r"^(AC-[0-9]+)\s*[:\-\.]?\s*(.*)$", re.IGNORECASE)


class JiraRequirementMapper:
    """
    Transforms a normalized JiraIssue into a canonical DevSense Requirement domain model.

    Invariants:
    1. Deterministic and pure transformation with no HTTP, network, or DB calls.
    2. Maps Jira key -> requirement_id and summary -> title.
    3. Derives source URI as '{base_url}/browse/{issue_key}'.
    4. Converts raw acceptance criteria strings into validated AcceptanceCriterion objects.
    5. Preserves explicit AC identifiers (e.g. 'AC-7') without renumbering.
    6. Generates sequential 'AC-<number>' identifiers for criteria without explicit IDs.
    7. Raises ValueError if JiraIssue.status is missing or empty.
    8. Preserves empty acceptance criteria without creating synthetic defaults.
    """

    def to_requirement(
        self,
        jira_issue: JiraIssue,
        base_url: str,
    ) -> Requirement:
        """
        Map a JiraIssue into a canonical Requirement domain model.

        Parameters
        ----------
        jira_issue : JiraIssue
            The normalized Jira issue to map.
        base_url : str
            The base URL of the Jira instance (e.g. 'https://company.atlassian.net').

        Returns
        -------
        Requirement
            The canonical domain Requirement.
        """
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")

        if not jira_issue.status or not jira_issue.status.strip():
            raise ValueError(
                f"Jira issue '{jira_issue.key}' status is required to construct a Requirement"
            )

        cleaned_base_url = base_url.strip().rstrip("/")
        source = f"{cleaned_base_url}/browse/{jira_issue.key}"

        acceptance_criteria = self._map_acceptance_criteria(
            jira_issue.acceptance_criteria
        )

        return Requirement(
            requirement_id=jira_issue.key,
            title=jira_issue.summary,
            description=jira_issue.description,
            source=source,
            priority=jira_issue.priority,
            status=jira_issue.status.strip(),
            acceptance_criteria=acceptance_criteria,
        )

    def _map_acceptance_criteria(
        self, raw_criteria: list[str]
    ) -> list[AcceptanceCriterion]:
        """
        Convert raw criterion strings into canonical AcceptanceCriterion domain objects.
        """
        if not raw_criteria:
            return []

        # Collect any explicit IDs up front to avoid collision with sequential generator
        explicit_ids: set[str] = set()
        for raw in raw_criteria:
            cleaned = raw.strip()
            match = _EXPLICIT_AC_PATTERN.match(cleaned)
            if match:
                explicit_ids.add(match.group(1).upper())

        criteria: list[AcceptanceCriterion] = []
        auto_counter = 1

        for raw in raw_criteria:
            cleaned = raw.strip()
            if not cleaned:
                continue

            match = _EXPLICIT_AC_PATTERN.match(cleaned)
            if match:
                criterion_id = match.group(1).upper()
                title = match.group(2).strip() or cleaned
            else:
                while f"AC-{auto_counter}" in explicit_ids:
                    auto_counter += 1
                criterion_id = f"AC-{auto_counter}"
                explicit_ids.add(criterion_id)
                auto_counter += 1
                title = cleaned

            criteria.append(
                AcceptanceCriterion(
                    criterion_id=criterion_id,
                    title=title,
                    description=None,
                )
            )

        return criteria
