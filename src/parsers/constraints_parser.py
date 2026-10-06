"""Constraints file parser — mixed structured tables and free-form business rules."""
from __future__ import annotations

import re
from pathlib import Path


def parse_constraints(file_path: str | Path) -> dict:
    """Parse a constraints file into structured and unstructured components.

    Returns dict with: field_constraints, business_rules, exclusions, errors, hil_needed
    """
    text = Path(file_path).read_text(encoding="utf-8")

    field_constraints = _extract_table_constraints(text)
    business_rules = _extract_business_rules(text)
    exclusions = _extract_exclusions(text)
    hil_needed = [r for r in business_rules if r.get("hil_required")]

    return {
        "field_constraints": field_constraints,
        "business_rules": business_rules,
        "exclusions": exclusions,
        "errors": [],
        "hil_needed": hil_needed,
    }


def _extract_table_constraints(text: str) -> list[dict]:
    """Extract structured table constraints (markdown table format)."""
    constraints = []
    # Match markdown table rows: | Field | Type | Constraint |
    table_pattern = re.compile(r'^\|\s*(\w[\w\s]*?)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|', re.MULTILINE)

    for match in table_pattern.finditer(text):
        field = match.group(1).strip()
        field_type = match.group(2).strip()
        constraint_text = match.group(3).strip()

        # Skip header separator rows
        if set(field) <= {'-', ' '} or field.lower() == 'field':
            continue

        constraint = {
            "field": field,
            "type": field_type,
            "rule_type": "regex",
            "parameters": {},
            "raw": constraint_text,
        }

        # Parse constraint types
        if constraint_text.startswith("regex:"):
            constraint["rule_type"] = "regex"
            constraint["parameters"]["pattern"] = constraint_text.split(":", 1)[1].strip()
        elif constraint_text.startswith("range:"):
            constraint["rule_type"] = "range"
            range_str = constraint_text.split(":", 1)[1].strip()
            parts = range_str.split("-")
            if len(parts) == 2:
                constraint["parameters"]["min"] = parts[0].strip()
                constraint["parameters"]["max"] = parts[1].strip()
        elif "valid" in constraint_text.lower():
            constraint["rule_type"] = "required"
            constraint["parameters"]["validation"] = constraint_text
        elif constraint_text.startswith("enum:"):
            constraint["rule_type"] = "enum"
            values = constraint_text.split(":", 1)[1].strip().split(",")
            constraint["parameters"]["values"] = [v.strip() for v in values]

        constraints.append(constraint)

    return constraints


def _extract_business_rules(text: str) -> list[dict]:
    """Extract free-form business rules (bullet points or numbered items)."""
    rules = []
    # Match lines starting with - or * or numbered
    rule_pattern = re.compile(r'^[\s]*[-*]\s+(.+)$', re.MULTILINE)

    # Only look in "Business Rules" section if it exists
    sections = re.split(r'^#\s+', text, flags=re.MULTILINE)
    rules_text = text
    for section in sections:
        if section.lower().startswith("business rule"):
            rules_text = section
            break

    for match in rule_pattern.finditer(rules_text):
        rule_text = match.group(1).strip()
        if not rule_text or rule_text.startswith("|"):
            continue

        # Determine if HIL is needed (complex rules that can't be auto-extracted)
        is_complex = any(kw in rule_text.lower() for kw in [
            "must be unique", "must not conflict", "depends on",
            "conditional", "if ", "when ", "unless ",
        ])

        rules.append({
            "description": rule_text,
            "extracted_rule": None if is_complex else rule_text,
            "hil_required": is_complex,
        })

    return rules


def _extract_exclusions(text: str) -> dict:
    """Extract sheet/column exclusion directives."""
    exclusions = {"sheets": [], "columns": []}

    # Match "exclude sheet: X" or "exclude column: Y"
    sheet_pattern = re.compile(r'exclude\s+sheet[s]?\s*:\s*(.+)', re.IGNORECASE)
    col_pattern = re.compile(r'exclude\s+column[s]?\s*:\s*(.+)', re.IGNORECASE)

    for match in sheet_pattern.finditer(text):
        sheets = [s.strip() for s in match.group(1).split(",")]
        exclusions["sheets"].extend(sheets)

    for match in col_pattern.finditer(text):
        cols = [c.strip() for c in match.group(1).split(",")]
        exclusions["columns"].extend(cols)

    return exclusions
