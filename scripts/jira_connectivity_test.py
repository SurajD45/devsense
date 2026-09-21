"""One-shot Jira connectivity test using local environment configuration."""

from app.integrations.jira import JiraClient
from app.integrations.jira.client import JiraClientError


def main() -> int:
    issue_key = input("Enter Jira issue key (for example ABC-123): ").strip()
    if not issue_key:
        print("[ERROR] Jira issue key is required.")
        return 1

    try:
        issue = JiraClient().get_issue(issue_key)
    except JiraClientError as exc:
        print(f"[ERROR] Jira request failed: {exc}")
        return 1
    except ValueError as exc:
        print(f"[ERROR] Invalid input: {exc}")
        return 1

    print("[OK] Jira connectivity verified")
    print(f"    Issue key : {issue.key}")
    print(f"    Summary   : {issue.summary}")
    print(f"    Status    : {issue.status or 'N/A'}")
    print(f"    Type      : {issue.issue_type or 'N/A'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
