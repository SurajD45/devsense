"""
Runtime verification: Jira API → JiraIssue → JiraRequirementMapper → Requirement.

Fetches the real Jira issue SCRUM-1 from the configured Jira instance and
maps it through the existing JiraRequirementMapper to produce a DevSense
Requirement domain model.  Prints all Requirement fields to verify the
complete runtime flow.

This script does NOT modify any production code, domain models, or tests.
"""

from app.integrations.jira import JiraClient, JiraRequirementMapper
from app.integrations.jira.client import JiraClientError
from app.settings import JIRA_BASE_URL

ISSUE_KEY = "SCRUM-5"

def main() -> int:
    # ── Step 1: Fetch JiraIssue from the real Jira instance ──────────────
    print(f"Fetching Jira issue {ISSUE_KEY} ...")

    try:
        client = JiraClient()
        jira_issue = client.get_issue(ISSUE_KEY)
    except JiraClientError as exc:
        print(f"[FAIL] Jira request failed: {exc}")
        return 1
    except ValueError as exc:
        print(f"[FAIL] Invalid input: {exc}")
        return 1

    print(f"[OK] JiraIssue retrieved: {jira_issue.key}")

    # -- Step 2: Map JiraIssue -> Requirement via JiraRequirementMapper ----
    try:
        mapper = JiraRequirementMapper()
        requirement = mapper.to_requirement(jira_issue, JIRA_BASE_URL)
    except (ValueError, TypeError) as exc:
        print(f"[FAIL] Mapping failed: {exc}")
        return 1

    print("[OK] Requirement mapped successfully")

    # -- Step 3: Print all Requirement fields ------------------------------
    print()
    print("===============================================")
    print("  DevSense Requirement")
    print("===============================================")
    print(f"  Requirement ID : {requirement.requirement_id}")
    print(f"  Title          : {requirement.title}")
    print(f"  Description    : {requirement.description or '(none)'}")
    print(f"  Priority       : {requirement.priority or '(none)'}")
    print(f"  Status         : {requirement.status}")
    print(f"  Source         : {requirement.source or '(none)'}")

    criteria = requirement.acceptance_criteria
    print(f"  Acceptance criteria count : {len(criteria)}")

    if criteria:
        print()
        print("  -- Acceptance Criteria --")
        for ac in criteria:
            print(f"     {ac.criterion_id} : {ac.title}")

    print()
    print("[OK] Runtime verification passed -- full pipeline works end-to-end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
