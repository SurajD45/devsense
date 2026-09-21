import re
from typing import Any

from app.integrations.jira.models import JiraIssue


class JiraResponseError(ValueError):
    """Raised when Jira returns an unexpected response shape."""


_AC_HEADING_PATTERN = re.compile(r"^(?:acceptance\s+criteria|ac)\s*:?$", re.IGNORECASE)


def _is_ac_heading(text: str) -> bool:
    """Check if a heading matches Acceptance Criteria variations."""
    cleaned = text.strip()
    return bool(_AC_HEADING_PATTERN.match(cleaned))


def _extract_node_text(node: Any) -> str:
    """Extract plain text from an arbitrary ADF node."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text" and isinstance(node.get("text"), str):
        return node["text"]
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == "text" and isinstance(n.get("text"), str):
                parts.append(n["text"])
            for child in n.get("content", []) or []:
                walk(child)
        elif isinstance(n, list):
            for child in n:
                walk(child)

    walk(node)
    return "".join(parts).strip()


def _extract_list_item_text(list_item_node: dict) -> str:
    """Extract readable text from a listItem ADF node."""
    lines: list[str] = []
    for block in list_item_node.get("content", []) or []:
        if isinstance(block, dict):
            block_text = _extract_node_text(block)
            if block_text:
                lines.append(block_text)
    return " ".join(lines).strip()


def _parse_text_criterion_item(line: str) -> str | None:
    """Parse a single criterion line if it represents a list item or explicit AC."""
    stripped = line.strip()
    if not stripped:
        return None

    # Bullet markers: - , * , +
    if stripped.startswith(("- ", "* ", "+ ")):
        return stripped[2:].strip()

    # Numbered list: 1. or 1)
    num_match = re.match(r"^\d+[\.\)]\s+(.*)$", stripped)
    if num_match:
        return num_match.group(1).strip()

    # Explicit AC identifier at line start: AC-1: ... or AC-1 ...
    ac_match = re.match(r"^(AC-\d+[:\s].*)$", stripped, re.IGNORECASE)
    if ac_match:
        return ac_match.group(1).strip()

    return None


def _is_text_ac_heading(line: str) -> bool:
    """Check if a text line represents an Acceptance Criteria heading."""
    stripped = line.strip()
    if stripped.startswith("#"):
        stripped = stripped.lstrip("#").strip()
    if (stripped.startswith("**") and stripped.endswith("**")) or (
        stripped.startswith("__") and stripped.endswith("__")
    ):
        stripped = stripped[2:-2].strip()
    elif (stripped.startswith("*") and stripped.endswith("*")) or (
        stripped.startswith("_") and stripped.endswith("_")
    ):
        stripped = stripped[1:-1].strip()
    return _is_ac_heading(stripped)


def _is_new_section(line: str) -> bool:
    """Check if a line marks the beginning of a new section in plain text."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    if (stripped.startswith("**") and stripped.endswith("**")) or (
        stripped.startswith("__") and stripped.endswith("__")
    ):
        return True
    if stripped.endswith(":") and len(stripped.split()) <= 6:
        return True
    return False


def _extract_adf_acceptance_criteria(adf: dict) -> list[str]:
    """Extract acceptance criteria from an ADF document structure."""
    content = adf.get("content", [])
    if not isinstance(content, list):
        return []

    criteria: list[str] = []
    in_ac_section = False

    for node in content:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")

        if node_type == "heading":
            heading_text = _extract_node_text(node)
            if _is_ac_heading(heading_text):
                in_ac_section = True
                continue
            elif in_ac_section:
                break

        elif in_ac_section and node_type in ("bulletList", "orderedList"):
            for item in node.get("content", []) or []:
                if isinstance(item, dict) and item.get("type") == "listItem":
                    item_text = _extract_list_item_text(item)
                    if item_text:
                        criteria.append(item_text)

        elif in_ac_section and node_type == "paragraph":
            p_text = _extract_node_text(node)
            item = _parse_text_criterion_item(p_text)
            if item:
                criteria.append(item)
            elif criteria and _is_new_section(p_text):
                break

    return criteria


def _extract_text_acceptance_criteria(text: str) -> list[str]:
    """Extract acceptance criteria from plain-text or markdown description."""
    if not text or not text.strip():
        return []

    lines = text.splitlines()
    criteria: list[str] = []
    in_ac_section = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if not in_ac_section:
            if _is_text_ac_heading(stripped):
                in_ac_section = True
            continue

        # Inside AC section: check if next meaningful section begins
        if _is_new_section(stripped):
            break

        item = _parse_text_criterion_item(stripped)
        if item:
            criteria.append(item)
        elif criteria:
            # Reached a non-item paragraph after criteria have been collected
            break

    return criteria


def extract_acceptance_criteria(description_field: Any) -> list[str]:
    """
    Extract Acceptance Criteria from a Jira description field (ADF or plain text).

    Returns an empty list if no Acceptance Criteria section is found.
    """
    if description_field is None:
        return []
    if isinstance(description_field, dict):
        criteria = _extract_adf_acceptance_criteria(description_field)
        if criteria:
            return criteria
        plain_text = _extract_adf_text(description_field)
        if plain_text:
            return _extract_text_acceptance_criteria(plain_text)
        return []
    if isinstance(description_field, str):
        return _extract_text_acceptance_criteria(description_field)
    return []


def _extract_adf_text(value: Any) -> str:
    """Extract readable text from Jira's Atlassian Document Format."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""

    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                parts.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return "\n".join(parts).strip()


def parse_issue(payload: Any) -> JiraIssue:
    """Convert a Jira issue JSON object into a normalized JiraIssue."""
    if not isinstance(payload, dict):
        raise JiraResponseError("Jira response must be a JSON object")

    key = payload.get("key")
    fields = payload.get("fields")
    if not isinstance(key, str) or not key.strip():
        raise JiraResponseError("Jira response is missing issue key")
    if not isinstance(fields, dict):
        raise JiraResponseError("Jira response is missing fields")

    summary = fields.get("summary")
    if not isinstance(summary, str):
        raise JiraResponseError("Jira response is missing a valid summary")

    status_value = fields.get("status")
    status = None
    if isinstance(status_value, dict):
        status_name = status_value.get("name")
        if status_name is not None and not isinstance(status_name, str):
            raise JiraResponseError("Jira response contains an invalid status")
        status = status_name

    issue_type_value = fields.get("issuetype")
    issue_type = None
    if isinstance(issue_type_value, dict):
        issue_type_name = issue_type_value.get("name")
        if issue_type_name is not None and not isinstance(issue_type_name, str):
            raise JiraResponseError("Jira response contains an invalid issue type")
        issue_type = issue_type_name

    priority_value = fields.get("priority")
    priority = None
    if isinstance(priority_value, dict):
        priority_name = priority_value.get("name")
        if priority_name is not None and not isinstance(priority_name, str):
            raise JiraResponseError("Jira response contains an invalid priority")
        priority = priority_name

    raw_description = fields.get("description")
    description = _extract_adf_text(raw_description)
    acceptance_criteria = extract_acceptance_criteria(raw_description)

    return JiraIssue(
        key=key.strip(),
        summary=summary.strip(),
        description=description or None,
        status=status,
        issue_type=issue_type,
        priority=priority,
        acceptance_criteria=acceptance_criteria,
    )

