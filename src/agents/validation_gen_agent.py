"""Step 3: Validation rules YAML generation."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


def _extract_yaml(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    match = re.search(r'```(?:yaml)?\s*\n(.*?)```', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _fix_yaml_quoting(yaml_text: str) -> str:
    """Fix common YAML quoting issues that break SnakeYAML.

    Fixes:
    - Mixed-quoted dotted refs: "Sheet Name".Column → "Sheet Name.Column"
    - Unquoted dotted refs with spaces: Sheet Name.Column → "Sheet Name.Column"
    """
    # Fix mixed quoting: "Sheet Name".Column → "Sheet Name.Column"
    # Matches: "quoted part".unquoted_rest
    yaml_text = re.sub(
        r'"([^"]+)"\.(\S+)',
        r'"\1.\2"',
        yaml_text,
    )
    # Fix unquoted dotted refs on from:/to: lines that contain spaces
    # e.g.  from: Some Sheet.Column → from: "Some Sheet.Column"
    def _quote_dotted_value(m):
        prefix = m.group(1)  # "from: " or "to: "
        value = m.group(2)
        if value.startswith('"'):
            return m.group(0)  # already quoted
        return f'{prefix}"{value}"'

    yaml_text = re.sub(
        r'((?:from|to):\s+)(\S+\.\S+)',
        _quote_dotted_value,
        yaml_text,
    )
    return yaml_text


def _validate_yaml(yaml_text: str) -> str:
    """Validate YAML and return it if valid, or raise with details."""
    from ruamel.yaml import YAML
    from io import StringIO
    yaml = YAML()
    yaml.load(StringIO(yaml_text))  # raises on parse error
    return yaml_text


def _strip_bad_workbook_rules(yaml_text: str, ciq_path: str) -> str:
    """Auto-remove workbook_rules that reference non-existent columns (e.g., set with bad partitionBy)."""
    import openpyxl
    from ruamel.yaml import YAML
    from io import StringIO

    yaml = YAML()
    try:
        rules = yaml.load(StringIO(yaml_text))
    except Exception:
        return yaml_text

    if not isinstance(rules, dict):
        return yaml_text

    # Build column lookup
    try:
        wb = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
    except Exception:
        return yaml_text

    ciq_cols_lower: dict[str, set[str]] = {}
    for sn in wb.sheetnames:
        ws = wb[sn]
        headers = set()
        for cell in next(ws.iter_rows(min_row=1, max_row=1), []):
            if cell.value:
                headers.add(str(cell.value).strip().lower())
        ciq_cols_lower[sn.lower()] = headers
    wb.close()

    wb_rules = rules.get("workbook_rules", [])
    if not wb_rules or not isinstance(wb_rules, list):
        return yaml_text

    cleaned = []
    removed = False
    for rule in wb_rules:
        if not isinstance(rule, dict):
            cleaned.append(rule)
            continue
        # Check set rules
        if "set" in rule:
            set_def = rule["set"]
            if isinstance(set_def, dict):
                to_def = set_def.get("to", {})
                if isinstance(to_def, dict):
                    to_sheet = str(to_def.get("sheet", "")).lower()
                    to_cols = ciq_cols_lower.get(to_sheet, set())
                    for pcol in (set_def.get("from", {}).get("partitionBy", []) or []):
                        if pcol.lower() not in to_cols:
                            removed = True
                            break
                    else:
                        cleaned.append(rule)
                        continue
                    continue  # skip this rule
        cleaned.append(rule)

    if removed:
        rules["workbook_rules"] = cleaned
        buf = StringIO()
        yaml_out = YAML()
        yaml_out.default_flow_style = False
        yaml_out.dump(rules, buf)
        logger.info("Auto-stripped invalid set workbook_rules")
        return buf.getvalue()

    return yaml_text


def _autofix_column_names(yaml_text: str, ciq_path: str) -> str:
    """Auto-fix column and sheet names in generated YAML to match actual CIQ.

    Fixes:
    1. Wrong column names → fuzzy-match to closest CIQ column
    2. Missing columns → add with default type: string
    3. Wrong sheet name casing → correct to actual CIQ sheet name
    """
    import openpyxl
    from ruamel.yaml import YAML
    from io import StringIO
    from difflib import get_close_matches

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        rules = yaml.load(StringIO(yaml_text))
    except Exception:
        return yaml_text

    if not isinstance(rules, dict):
        return yaml_text

    try:
        wb = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
    except Exception:
        return yaml_text

    # Build CIQ structure
    ciq_sheets: dict[str, list[str]] = {}  # original_name → [columns]
    ciq_sheet_map: dict[str, str] = {}     # lower → original
    for sn in wb.sheetnames:
        ws = wb[sn]
        headers = []
        for cell in next(ws.iter_rows(min_row=1, max_row=1), []):
            if cell.value:
                headers.append(str(cell.value).strip())
        ciq_sheets[sn] = headers
        ciq_sheet_map[sn.lower()] = sn
    wb.close()

    sheets_def = rules.get("sheets", {})
    if not sheets_def or not isinstance(sheets_def, dict):
        return yaml_text

    fixed = False
    new_sheets = {}
    # Track renames per sheet: {sheet_lower: {old_col_lower: new_col_actual}}
    col_rename_map: dict[str, dict[str, str]] = {}

    for rule_sheet, sheet_def in sheets_def.items():
        actual_sheet = ciq_sheet_map.get(rule_sheet.lower())
        if not actual_sheet:
            new_sheets[rule_sheet] = sheet_def
            continue

        target_sheet = actual_sheet
        if not isinstance(sheet_def, dict):
            new_sheets[target_sheet] = sheet_def
            if target_sheet != rule_sheet:
                fixed = True
            continue

        columns = sheet_def.get("columns", {})
        if not columns or not isinstance(columns, dict):
            new_sheets[target_sheet] = sheet_def
            if target_sheet != rule_sheet:
                fixed = True
            continue

        actual_cols = ciq_sheets.get(actual_sheet, [])
        actual_cols_lower = {c.lower(): c for c in actual_cols}
        used_cols_lower = set()
        sheet_renames: dict[str, str] = {}  # old_name → new_name

        new_columns = {}
        for col_name, col_def in columns.items():
            cl = col_name.lower()
            if cl in actual_cols_lower:
                correct_name = actual_cols_lower[cl]
                new_columns[correct_name] = col_def
                used_cols_lower.add(cl)
                if correct_name != col_name:
                    sheet_renames[col_name] = correct_name
                    fixed = True
            else:
                matches = get_close_matches(cl, list(actual_cols_lower.keys()), n=1, cutoff=0.6)
                if matches:
                    correct_name = actual_cols_lower[matches[0]]
                    new_columns[correct_name] = col_def
                    used_cols_lower.add(matches[0])
                    sheet_renames[col_name] = correct_name
                    fixed = True
                    logger.info("Auto-fix: '%s' → '%s' in sheet '%s'", col_name, correct_name, actual_sheet)
                else:
                    # No match — drop invented column, map to first unused actual col if possible
                    remaining = [c for c in actual_cols if c.lower() not in used_cols_lower]
                    if remaining:
                        correct_name = remaining[0]
                        new_columns[correct_name] = col_def
                        used_cols_lower.add(correct_name.lower())
                        sheet_renames[col_name] = correct_name
                        logger.info("Auto-fix: '%s' → '%s' (fallback) in sheet '%s'", col_name, correct_name, actual_sheet)
                    else:
                        logger.info("Auto-fix: dropped invented column '%s' from sheet '%s'", col_name, actual_sheet)
                    fixed = True

        # Add missing CIQ columns
        for actual_col in actual_cols:
            if actual_col.lower() not in used_cols_lower:
                new_columns[actual_col] = {"type": "string"}
                fixed = True
                logger.info("Auto-fix: added missing column '%s' to sheet '%s'", actual_col, actual_sheet)

        # Fix row rules (unique_key, one_of, etc.)
        sheet_rules = sheet_def.get("rules", [])
        if sheet_rules and isinstance(sheet_rules, list) and sheet_renames:
            for rule in sheet_rules:
                if isinstance(rule, dict):
                    for rule_key in ("unique_key", "one_of"):
                        if rule_key in rule and isinstance(rule[rule_key], list):
                            rule[rule_key] = [
                                sheet_renames.get(c, c) for c in rule[rule_key]
                            ]
                    # Fix require/forbid column refs
                    for rule_key in ("require", "forbid"):
                        if rule_key in rule and isinstance(rule[rule_key], str):
                            rule[rule_key] = sheet_renames.get(rule[rule_key], rule[rule_key])
                    # Fix when.column
                    when = rule.get("when")
                    if isinstance(when, dict) and "column" in when:
                        when["column"] = sheet_renames.get(when["column"], when["column"])
                    # Fix minOnePerGroup.groupByColumn
                    for col_def in new_columns.values():
                        if isinstance(col_def, dict):
                            mog = col_def.get("minOnePerGroup")
                            if isinstance(mog, dict) and "groupByColumn" in mog:
                                mog["groupByColumn"] = sheet_renames.get(mog["groupByColumn"], mog["groupByColumn"])

        sheet_def["columns"] = new_columns
        new_sheets[target_sheet] = sheet_def
        col_rename_map[actual_sheet.lower()] = sheet_renames
        if target_sheet != rule_sheet:
            fixed = True

    # Auto-fix required columns that have blank values in CIQ data
    try:
        wb2 = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
        for sheet_name, sheet_def in new_sheets.items():
            if not isinstance(sheet_def, dict):
                continue
            sn_lower = sheet_name.lower()
            actual_sn = ciq_sheet_map.get(sn_lower)
            if not actual_sn or actual_sn not in wb2.sheetnames:
                continue
            ws = wb2[actual_sn]
            columns = sheet_def.get("columns", {})
            if not columns:
                continue
            header_row = list(next(ws.iter_rows(min_row=1, max_row=1), []))
            header_map = {}
            for idx, cell in enumerate(header_row):
                if cell.value:
                    header_map[str(cell.value).strip().lower()] = idx
            # Collect actual values per column for validation
            col_values: dict[str, set] = {}
            data_rows = list(ws.iter_rows(min_row=2, values_only=True))
            for col_name_lc, col_idx in header_map.items():
                vals = set()
                for row in data_rows:
                    if not any(c is not None and str(c).strip() for c in row):
                        continue
                    if col_idx < len(row) and row[col_idx] is not None:
                        vals.add(str(row[col_idx]).strip())
                col_values[col_name_lc] = vals

            for col_name, col_def in columns.items():
                if not isinstance(col_def, dict):
                    continue
                col_idx = header_map.get(col_name.lower())
                if col_idx is None:
                    continue

                # Fix required:true on columns with blanks
                if col_def.get("required"):
                    for row in data_rows:
                        if not any(c is not None and str(c).strip() for c in row):
                            continue
                        if col_idx < len(row):
                            val = row[col_idx]
                            if val is None or (isinstance(val, str) and not val.strip()):
                                col_def["required"] = False
                                fixed = True
                                logger.info("Auto-fix: '%s' in '%s' → required:false (has blanks)", col_name, sheet_name)
                                break

                # Remove allowedValues that don't match actual CIQ data
                allowed = col_def.get("allowedValues")
                if allowed and isinstance(allowed, list):
                    actual_vals = col_values.get(col_name.lower(), set())
                    if actual_vals:
                        unmatched = actual_vals - set(str(a) for a in allowed)
                        if unmatched:
                            del col_def["allowedValues"]
                            fixed = True
                            logger.info("Auto-fix: removed allowedValues from '%s' in '%s' (CIQ has values not in list: %s)",
                                        col_name, sheet_name, list(unmatched)[:3])

                # Fix wrong type — if type:integer but column has non-numeric values, change to string
                col_type = col_def.get("type")
                if col_type in ("integer", "decimal"):
                    actual_vals = col_values.get(col_name.lower(), set())
                    if actual_vals:
                        non_numeric = [v for v in actual_vals if v and not v.replace(".", "").replace("-", "").isdigit()]
                        if non_numeric:
                            col_def["type"] = "string"
                            fixed = True
                            logger.info("Auto-fix: '%s' in '%s' → type:string (has non-numeric: %s)",
                                        col_name, sheet_name, non_numeric[:3])

                # Remove pattern if actual values don't match it
                import re as _re
                pattern = col_def.get("pattern")
                if pattern and isinstance(pattern, str):
                    actual_vals = col_values.get(col_name.lower(), set())
                    if actual_vals:
                        try:
                            pat = _re.compile(pattern)
                            mismatches = [v for v in actual_vals if v and not pat.match(v)]
                            if mismatches:
                                del col_def["pattern"]
                                fixed = True
                                logger.info("Auto-fix: removed pattern from '%s' in '%s' (values don't match: %s)",
                                            col_name, sheet_name, mismatches[:3])
                        except _re.error:
                            del col_def["pattern"]
                            fixed = True

        # Auto-fix unique_key rules — remove keys that have duplicates or blanks
        for sheet_name, sheet_def in new_sheets.items():
            if not isinstance(sheet_def, dict):
                continue
            actual_sn = ciq_sheet_map.get(sheet_name.lower())
            if not actual_sn or actual_sn not in wb2.sheetnames:
                continue
            ws = wb2[actual_sn]
            header_row = list(next(ws.iter_rows(min_row=1, max_row=1), []))
            header_map = {}
            for idx, cell in enumerate(header_row):
                if cell.value:
                    header_map[str(cell.value).strip().lower()] = idx

            sheet_rules = sheet_def.get("rules", [])
            if not sheet_rules or not isinstance(sheet_rules, list):
                continue

            new_rules = []
            for rule in sheet_rules:
                if not isinstance(rule, dict) or "unique_key" not in rule:
                    new_rules.append(rule)
                    continue
                uk_cols = rule["unique_key"]
                if not isinstance(uk_cols, list):
                    new_rules.append(rule)
                    continue
                # Check if unique_key columns actually form a unique key in CIQ data
                col_indices = []
                valid = True
                for uc in uk_cols:
                    idx = header_map.get(uc.lower())
                    if idx is None:
                        valid = False
                        break
                    col_indices.append(idx)
                if not valid:
                    fixed = True
                    logger.info("Auto-fix: removed unique_key %s from '%s' (column not found)", uk_cols, sheet_name)
                    continue
                # Check for duplicates
                seen_keys = set()
                has_dup = False
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not any(c is not None and str(c).strip() for c in row):
                        continue
                    key = tuple(
                        str(row[i]).strip() if i < len(row) and row[i] is not None else ""
                        for i in col_indices
                    )
                    if key in seen_keys:
                        has_dup = True
                        break
                    seen_keys.add(key)
                if has_dup:
                    fixed = True
                    logger.info("Auto-fix: removed unique_key %s from '%s' (has duplicates in CIQ)", uk_cols, sheet_name)
                else:
                    new_rules.append(rule)
            if len(new_rules) != len(sheet_rules):
                sheet_def["rules"] = new_rules if new_rules else None
                if not new_rules and "rules" in sheet_def:
                    del sheet_def["rules"]

        wb2.close()
    except Exception:
        pass

    # Add missing CIQ sheets
    rules_sheets_lower = {s.lower() for s in new_sheets}
    for ciq_sn, ciq_cols in ciq_sheets.items():
        if ciq_sn.lower() not in rules_sheets_lower:
            new_sheets[ciq_sn] = {
                "required": True,
                "columns": {c: {"type": "string"} for c in ciq_cols},
            }
            fixed = True
            logger.info("Auto-fix: added missing sheet '%s' with %d columns", ciq_sn, len(ciq_cols))

    if fixed:
        rules["sheets"] = new_sheets
        # Build full col maps for workbook_rules fix
        col_maps = {}
        for sn, cols in ciq_sheets.items():
            renames = col_rename_map.get(sn.lower(), {})
            col_map = {c.lower(): c for c in cols}
            # Also add old→new renames (lowercase old → actual new)
            for old, new in renames.items():
                col_map[old.lower()] = new
            col_maps[sn] = col_map
        _fix_workbook_rule_refs(rules, ciq_sheet_map, col_maps)
        buf = StringIO()
        yaml_out = YAML()
        yaml_out.default_flow_style = False
        yaml_out.dump(rules, buf)
        result = buf.getvalue()
        logger.info("Auto-fix applied: corrected column/sheet names and rule references")
        return result

    return yaml_text


def _fix_workbook_rule_refs(rules: dict, sheet_map: dict, col_maps: dict):
    """Fix sheet/column references in workbook_rules after auto-fix."""
    wb_rules = rules.get("workbook_rules", [])
    if not wb_rules:
        return
    for rule in wb_rules:
        if not isinstance(rule, dict):
            continue
        for key in ("subset", "set"):
            if key not in rule:
                continue
            ref = rule[key]
            if not isinstance(ref, dict):
                continue
            for direction in ("from", "to"):
                val = ref.get(direction)
                if isinstance(val, str) and "." in val:
                    # Dotted ref like "Index.Node"
                    parts = val.split(".", 1)
                    sheet_lower = parts[0].lower()
                    actual_sheet = sheet_map.get(sheet_lower, parts[0])
                    col_lower = parts[1].lower()
                    actual_col = col_maps.get(actual_sheet, {}).get(col_lower, parts[1])
                    ref[direction] = f"{actual_sheet}.{actual_col}"
                elif isinstance(val, dict):
                    sheet = str(val.get("sheet", ""))
                    actual_sheet = sheet_map.get(sheet.lower(), sheet)
                    val["sheet"] = actual_sheet
                    col = str(val.get("column", ""))
                    actual_col = col_maps.get(actual_sheet, {}).get(col.lower(), col)
                    val["column"] = actual_col


def _validate_against_ciq(yaml_text: str, ciq_path: str) -> list[str]:
    """Check that sheet/column names in validation rules match the actual CIQ.

    Returns a list of mismatch descriptions, empty if all OK.
    """
    import openpyxl
    from ruamel.yaml import YAML
    from io import StringIO

    yaml = YAML()
    rules = yaml.load(StringIO(yaml_text))
    if not isinstance(rules, dict):
        return []

    # Read actual CIQ structure
    try:
        wb = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
    except Exception:
        return []  # can't validate — skip

    ciq_sheets: dict[str, list[str]] = {}
    for sn in wb.sheetnames:
        ws = wb[sn]
        headers = []
        for cell in next(ws.iter_rows(min_row=1, max_row=1), []):
            if cell.value:
                headers.append(str(cell.value).strip())
        ciq_sheets[sn.lower()] = headers
        ciq_sheets[sn] = headers  # keep original case too
    wb.close()

    # Build case-insensitive lookup
    ciq_sheet_names_lower = {s.lower(): s for s in wb.sheetnames}
    ciq_columns_lower: dict[str, dict[str, str]] = {}  # sheet_lower → {col_lower: col_original}
    for sn in wb.sheetnames:
        ciq_columns_lower[sn.lower()] = {
            h.lower(): h for h in ciq_sheets.get(sn, [])
        }

    mismatches = []
    sheets_def = rules.get("sheets", {}) or {}

    # Check completeness — every CIQ sheet must be in the rules
    rules_sheets_lower = {s.lower() for s in sheets_def}
    for ciq_sn in wb.sheetnames:
        if ciq_sn.lower() not in rules_sheets_lower:
            cols = ciq_sheets.get(ciq_sn, [])
            mismatches.append(
                f"CIQ sheet '{ciq_sn}' is MISSING from rules. "
                f"You must add it with columns: {cols}"
            )

    # Check each sheet in rules
    for sheet_name, sheet_def in sheets_def.items():
        sn_lower = sheet_name.lower()
        if sn_lower not in ciq_sheet_names_lower:
            mismatches.append(
                f"Sheet '{sheet_name}' in rules not found in CIQ. "
                f"Available sheets: {list(wb.sheetnames)}"
            )
            continue

        if not isinstance(sheet_def, dict):
            continue
        columns = sheet_def.get("columns", {}) or {}
        actual_cols = ciq_columns_lower.get(sn_lower, {})
        actual_sheet = ciq_sheet_names_lower[sn_lower]

        # Check for wrong column names
        for col_name in columns:
            if col_name.lower() not in actual_cols:
                mismatches.append(
                    f"Column '{col_name}' in sheet '{sheet_name}' not found in CIQ. "
                    f"Available columns in '{actual_sheet}': {ciq_sheets.get(actual_sheet, [])}"
                )

        # Check completeness — every CIQ column should be in the rules
        for actual_col in ciq_sheets.get(actual_sheet, []):
            if actual_col.lower() not in {c.lower() for c in columns}:
                mismatches.append(
                    f"CIQ column '{actual_col}' in sheet '{actual_sheet}' is MISSING from rules. Add it."
                )

    # Check required columns against actual CIQ data — required columns must not have blanks
    try:
        wb2 = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
        for sheet_name, sheet_def in sheets_def.items():
            if not isinstance(sheet_def, dict):
                continue
            sn_lower = sheet_name.lower()
            actual_sn = ciq_sheet_names_lower.get(sn_lower)
            if not actual_sn or actual_sn not in wb2.sheetnames:
                continue
            ws = wb2[actual_sn]
            columns = sheet_def.get("columns", {}) or {}
            # Read headers
            header_row = list(next(ws.iter_rows(min_row=1, max_row=1), []))
            header_map = {}
            for idx, cell in enumerate(header_row):
                if cell.value:
                    header_map[str(cell.value).strip().lower()] = idx

            for col_name, col_def in columns.items():
                if not isinstance(col_def, dict) or not col_def.get("required"):
                    continue
                col_lower = col_name.lower()
                if col_lower not in header_map:
                    continue
                col_idx = header_map[col_lower]
                # Check if any non-blank data row has a blank value in this column
                has_blank = False
                for row in ws.iter_rows(min_row=2, values_only=True):
                    # Skip fully blank rows (ignoreBlankRows: true)
                    if not any(c is not None and str(c).strip() for c in row):
                        continue
                    if col_idx < len(row):
                        val = row[col_idx]
                        if val is None or (isinstance(val, str) and not val.strip()):
                            has_blank = True
                            break
                if has_blank:
                    mismatches.append(
                        f"Column '{col_name}' in sheet '{sheet_name}' is marked required:true "
                        f"but has blank values in CIQ data. Change to required:false "
                        f"(use a one_of or row rule if at least one of a group must be filled)."
                    )
        wb2.close()
    except Exception:
        pass

    # Validate row rule structure
    # Validate workbook_rules — check set rules have valid partition columns
    wb_rules = rules.get("workbook_rules", []) or []
    for wb_rule in wb_rules:
        if not isinstance(wb_rule, dict):
            continue
        if "set" in wb_rule:
            set_def = wb_rule["set"]
            if isinstance(set_def, dict):
                from_def = set_def.get("from", {})
                to_def = set_def.get("to", {})
                if isinstance(from_def, dict) and isinstance(to_def, dict):
                    for part_col in (from_def.get("partitionBy", []) or []):
                        from_sheet = from_def.get("sheet", "")
                        to_sheet = to_def.get("sheet", "")
                        # Check partition column exists in target sheet
                        to_sheet_lower = to_sheet.lower() if to_sheet else ""
                        to_cols = ciq_columns_lower.get(to_sheet_lower, {})
                        if part_col.lower() not in to_cols:
                            mismatches.append(
                                f"workbook_rules 'set' rule: partitionBy column '{part_col}' "
                                f"does not exist in target sheet '{to_sheet}'. "
                                f"Available columns: {list(to_cols.values())}. "
                                f"Remove this set rule or use a simple subset rule instead."
                            )

    # Validate row rule structure
    VALID_RULE_KEYS = {"unique_key", "one_of", "all_or_none", "require", "forbid",
                       "only_one", "sum", "equals", "pooled_unique", "compare"}
    RULES_WITH_WHEN = {"require", "forbid", "compare"}
    for sheet_name, sheet_def in sheets_def.items():
        if not isinstance(sheet_def, dict):
            continue
        sheet_rules = sheet_def.get("rules", []) or []
        for rule in sheet_rules:
            if not isinstance(rule, dict):
                continue
            rule_keys = set(rule.keys()) - {"when", "message"}
            unknown = rule_keys - VALID_RULE_KEYS
            if unknown:
                mismatches.append(
                    f"Unknown row rule key {unknown} in sheet '{sheet_name}'. "
                    f"Valid keys: {sorted(VALID_RULE_KEYS)}"
                )
            # forbid/require must be a single column name, not a list
            for key in ("forbid", "require"):
                if key in rule and isinstance(rule[key], list):
                    mismatches.append(
                        f"'{key}' in sheet '{sheet_name}' must be a single column name (string), not a list. "
                        f"Got: {rule[key]}"
                    )
            # when condition validation — only for require/forbid/compare
            has_when_rule = rule_keys & RULES_WITH_WHEN
            when = rule.get("when")
            if has_when_rule and isinstance(when, dict) and when and "column" not in when:
                mismatches.append(
                    f"'when' condition in sheet '{sheet_name}' must use {{column: X, operator: Y, value: Z}} format. "
                    f"Got: {when}"
                )

    return mismatches


class ValidationGenAgent:
    step_number = 3
    step_name = "Validation Rules Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(3, config)

        schema_path = Path("schemas/validation_rules_schema.yaml")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

        prompt_path = Path("src/prompts/validation_gen.yaml")
        prompt_template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        few_shot_text = ""
        if few_shot_examples:
            for ex in few_shot_examples[:3]:
                if "validation_rules" in ex:
                    few_shot_text += (
                        f"\n--- Example from UC '{ex['uc_name']}' ---\n"
                        f"{ex['validation_rules']}\n"
                    )

        # Build per-sheet CIQ summary for the prompt
        ciq_data = input_data.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})
        sheet_names = ciq_data.get("sheet_names", [])

        if per_sheet:
            ciq_summary_parts = [f"CIQ Workbook Sheets: {sheet_names}\n"]
            # Build a strict column map for emphasis
            column_map_parts = [
                "*** EXACT COLUMN NAMES (you MUST use these, not the example's) ***"
            ]
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
                samples = sdata.get("sample_rows", []) if isinstance(sdata, dict) else getattr(sdata, "sample_rows", [])
                column_map_parts.append(f'  Sheet "{sname}" → columns: {headers}')
                ciq_summary_parts.append(f'\nSheet: "{sname}"\n  Columns: {headers}')
                if samples:
                    ciq_summary_parts.append(f"  Sample rows ({len(samples)}):")
                    for r in samples[:3]:
                        vals = {k: v for k, v in (r.items() if isinstance(r, dict) else r) if v}
                        ciq_summary_parts.append(f"    {vals}")
            column_map_parts.append("*** DO NOT use column names from the example — use ONLY the names above ***\n")
            ir_summary = "\n".join(column_map_parts) + "\n\n" + "\n".join(ciq_summary_parts)
        else:
            ir_summary = json.dumps(
                {k: input_data.get(k) for k in ("uc_metadata", "ciq_data")},
                indent=2, default=str,
            )[:6000]

        retry_info = ""
        if retry_context:
            retry_info = (
                f"\n\nPREVIOUS ATTEMPT FAILED:\n"
                f"{json.dumps(retry_context.previous_error, indent=2)}\n"
                "Fix the issues and regenerate."
            )

        # QA feedback from Step 5 — failed test cases from previous run
        qa_feedback = ""
        qa_data = input_data.get("qa_feedback", [])
        logger.info("Step 3: qa_feedback has %d failed TCs", len(qa_data) if qa_data else 0)
        if qa_data:
            qa_lines = [
                "\n\n=== QA TEST FEEDBACK (from previous Step 5 run) ===",
                "The following test cases FAILED during CIQ validation QA.",
                "Fix the validation rules to address these issues:\n",
            ]
            for tc in qa_data:
                tc_id = tc.get("id", "?")
                scenario = tc.get("scenario", "")
                expected = tc.get("expected", "")
                actual = tc.get("actual", "")
                remarks = tc.get("remarks", "")
                qa_lines.append(f"  [{tc_id}] {scenario}")
                qa_lines.append(f"    Expected: {expected}, Actual: {actual}")
                qa_lines.append(f"    Remarks: {remarks}")
                # Include JAR stderr for context
                stderr = tc.get("stderr", "").strip()
                if stderr:
                    qa_lines.append(f"    JAR error: {stderr[:500]}")
                qa_lines.append("")

            qa_lines.append(
                "IMPORTANT: Adjust the validation rules so that ALL test cases pass.\n"
                "- If a negative test (expected FAIL) passed instead, the rule is too lenient — tighten it.\n"
                "- If a positive test (expected PASS) failed instead, the rule is too strict — relax it.\n"
                "- For 'optional fields blank' tests: columns involved in one_of/minOnePerGroup "
                "row rules are NOT truly optional — mark them required:false but ensure the "
                "row-level rules still make sense when individual columns are blank.\n"
            )
            qa_feedback = "\n".join(qa_lines)

        uc_name = input_data.get("uc_name", "UC")
        parts = uc_name.split("_", 1)
        node_type = parts[0] if len(parts) == 2 else uc_name
        activity = parts[1] if len(parts) == 2 else ""

        # Real example for few-shot (embedded)
        # NOTE: column names (Node, NIAM NAME, etc.) are from a DIFFERENT UC — not this one
        example = '''# Example from a DIFFERENT UC (SBC_33_LIC_LOADING_IN_SBC) — column names differ from yours!
# Use this ONLY for YAML structure reference. Use YOUR CIQ's actual column names.
version: "1.0"
skipValidationValues: [MANO_EMPTY]

settings:
  headerRow: 0
  dataStartRow: 1
  trimCellValues: false
  ignoreBlankRows: true
  caseSensitiveHeaders: false
  caseSensitiveValues: true

workbook_rules:
  - subset:
        from: "Index.Node"
        to: "IP.Node"
  - set:
      bidirectional: true
      from:
        sheet: Index
        column: Node
        partitionBy: [Node]
      to:
        sheet: LICENSE_FILE
        column: Node
        partitionBy: [Node]

sheets:
  Index:
    required: true
    columns:
      Node:
        required: true
        type: string
      CRGROUP:
        required: true
        type: string
        pattern: "^CR[0-9]+$"
      EMAIL:
        type: email
        multi: true
        consolidate: true
        consolidate_separator: ";"
        minOnePerGroup:
          groupByColumn: CRGROUP
    rules:
      - unique_key: [Node, CRGROUP]

  IP:
    required: true
    columns:
      Node:
        required: true
        type: string
      NIAM NAME:
        required: true
        type: string
    rules:
      - unique_key: [Node, NIAM NAME]

  LICENSE_FILE:
    required: true
    columns:
      Node:
        required: true
        type: string
      LICENSE_FILE:
        required: true
        type: string
        unique: true
    rules:
      - unique_key: [Node, LICENSE_FILE]

outputs:
  CR_LIST:
    sheet: Index
    column: CRGROUP
    aggregate: distinct
    separator: ","
  CR_COUNT:
    sheet: Index
    column: CRGROUP
    aggregate: distinct_count

report_output:
  filename: "{nodeType}_{activity}_VALIDATION_REPORT"'''

        # Build constraints summary
        constraints_data = input_data.get("constraints")
        constraints_text = ""
        if constraints_data:
            constraints_text = json.dumps(constraints_data, indent=2, default=str)[:3000]

        # Build a strict column reference block to place at the TOP of the prompt
        column_ref_lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  MANDATORY: USE ONLY THESE EXACT COLUMN & SHEET NAMES      ║",
            "╚══════════════════════════════════════════════════════════════╝",
        ]
        if per_sheet:
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
                column_ref_lines.append(f'  Sheet "{sname}" columns: {headers}')
            column_ref_lines.append("")
            column_ref_lines.append("RULES:")
            column_ref_lines.append("- Copy-paste these names EXACTLY into your YAML")
            column_ref_lines.append("- DO NOT rename, abbreviate, or invent column names")
            column_ref_lines.append("- Every column listed above MUST appear in your sheets section")
            column_ref_lines.append("- The example below uses DIFFERENT column names — ignore its names, use YOURS")
        column_ref = "\n".join(column_ref_lines)

        prompt = (
            f"Generate a validation-rules.yaml for UC: {uc_name}\n"
            f"Node Type: {node_type}, Activity: {activity}\n\n"

            f"{column_ref}\n\n"

            f"{retry_info}{qa_feedback}\n\n"

            f"=== CIQ DATA (sheets, headers, sample rows) ===\n{ir_summary}\n\n"

            f"=== USER CONSTRAINTS ===\n{constraints_text or 'None provided'}\n\n"

            f"=== REAL WORKING EXAMPLE (STRUCTURE REFERENCE ONLY — column names differ from yours!) ===\n{example}\n\n"

            f"=== SCHEMA REFERENCE (column types, workbook_rules, etc.) ===\n{schema[:8000]}\n\n"

            f"{few_shot_text}\n\n"

            "=== GENERATION INSTRUCTIONS ===\n\n"

            "Generate the COMPLETE validation-rules.yaml with this EXACT top-level structure:\n\n"

            "```\n"
            "# Header comment: Validation Rules: {NODE_TYPE} - {ACTIVITY}\n"
            "version: \"1.0\"\n"
            "settings: { headerRow: 0, dataStartRow: 1, trimCellValues: true, ignoreBlankRows: true, caseSensitiveHeaders: false, caseSensitiveValues: true }\n"
            "workbook_rules: [ ... ]     # cross-sheet rules\n"
            "sheets: { ... }             # per-sheet column definitions + row rules\n"
            "outputs: { ... }            # post-validation computed values\n"
            "report_output: { ... }      # report file naming\n"
            "```\n\n"

            "=== TELECOM CIQ KNOWLEDGE (apply these rules) ===\n\n"

            "STANDARD SHEETS:\n"
            "- Index sheet (or similar): Contains CRGroup (CR number/name), Node/NodeGroup/Zone (grouping), "
            "EMAIL (notification contact), optionally Tables (data sheet references for execution sequence).\n"
            "- IP/Node_Details sheet: Always present. Maps node hostnames to login details — "
            "NIAM NAME/NIAM ID (Nokia login), IP addresses, may have passwords, software versions. "
            "Used for node grouping. Can have multiple NIAM IDs per row.\n"
            "- Data sheets: UC-specific configuration data (the actual changes to apply).\n\n"

            "WORKBOOK_RULES:\n"
            "- ONLY use 'subset' rules for cross-sheet validation — they are simple and reliable\n"
            "- Example: Index grouping column must be a subset of IP grouping column\n"
            "- DO NOT use 'set' rules with partitionBy unless BOTH sheets have the partition column\n"
            "- DO NOT add set/bidirectional rules — they are complex and error-prone\n"
            "- Keep workbook_rules minimal — only add what's clearly needed\n\n"

            "FIXED COLUMN RULES (always include for standard columns):\n"
            "- EMAIL columns: type: email, multi: true, consolidate: true, consolidate_separator: \";\", "
            "minOnePerGroup: { groupByColumn: CRGroup }\n"
            "- CRGroup columns: type: string, pattern: \"^CR[0-9]+$\" (if CR-numbered)\n"
            "- Node/hostname columns: type: string, required: true\n"
            "- NIAM NAME columns: type: string, required: true\n\n"

            "TYPE INFERENCE RULES:\n"
            "- DEFAULT: If type cannot be derived, use type: string — always use a primitive type (string, integer, decimal, boolean)\n"
            "- DEFAULT REQUIRED: All columns are required: true UNLESS explicitly marked optional in constraints or column values are mostly empty in sample data\n"
            "- DO NOT generate pattern regex from sample data values — patterns are too fragile\n"
            "- ONLY add pattern regex for these specific cases:\n"
            "  - CRGroup columns: pattern: \"^CR[0-9]+$\" (if values look like CR001, CR002)\n"
            "  - File path columns: pattern: \"^/.*\" (if values are Unix paths)\n"
            "  - Columns where user explicitly specifies a pattern in constraints\n"
            "- TYPE from column name: EMAIL → type: email | IP_ADDR/IP → type: ip | PORT → type: integer | HOSTNAME → type: hostname\n"
            "- TYPE from cell values: \"192.168.1.1\" → type: ip | \"user@nokia.com\" → type: email | all numeric → type: integer\n"
            "- NIAM NAME columns: type: string, required: true — NO pattern regex\n"
            "- Zone/NodeGroup/Node columns: type: string, required: true — NO pattern regex, NO allowedValues from sample data\n"
            "- DO NOT extract allowedValues from sample data rows — only use allowedValues when user specifies them in constraints\n\n"
            "MANO_EMPTY HANDLING:\n"
            "- If 'MANO_EMPTY' appears in sample data values, it is a sentinel meaning 'set empty value in network config'\n"
            "- Add skipValidationValues: [MANO_EMPTY] at root level when MANO_EMPTY is seen in any column data\n"
            "- Columns containing MANO_EMPTY should still be typed as string — MANO_EMPTY is a valid string constant\n\n"

            "ROW RULES (per sheet) — EXACT SYNTAX:\n"
            "- unique_key: [ColA, ColB]    — composite primary key (ALWAYS include for natural keys)\n"
            "- one_of: [ColA, ColB]        — at least one must be non-blank (use when CIQ data shows alternating blanks)\n"
            "- ONLY add require/forbid/all_or_none if USER CONSTRAINTS explicitly require them\n"
            "- DO NOT invent require/forbid/all_or_none rules from sample data — they will cause false failures\n"
            "- DO NOT add all_or_none for groups of columns unless explicitly requested in constraints\n"
            "- DO NOT add forbid rules unless explicitly requested in constraints\n"
            "- If you must use require/forbid, the EXACT syntax is:\n"
            "    - require: ColName\n"
            "      when:\n"
            "        column: TriggerCol\n"
            "        operator: notBlank       # equals|notEquals|blank|notBlank\n"
            "    - forbid: ColName   (SINGLE string, NOT a list)\n"
            "      when:\n"
            "        column: TriggerCol\n"
            "        operator: equals\n"
            "        value: \"someValue\"\n"
            "- DO NOT use forbid: [list] — forbid takes a SINGLE column name\n"
            "- DO NOT use when: {ColName: {not_equals: val}} — use column/operator/value format\n\n"

            "OUTPUTS (always include):\n"
            "- CR_LIST: { sheet: <Index sheet name>, column: <CRGroup column>, aggregate: distinct, separator: \",\" }\n"
            "- CR_COUNT: { sheet: <Index sheet name>, column: <CRGroup column>, aggregate: distinct_count }\n\n"

            "REPORT_OUTPUT (always include):\n"
            "- filename: \"{nodeType}_{activity}_VALIDATION_REPORT\" — use LITERAL placeholders {nodeType} and {activity}, NOT the actual UC name\n\n"

            "YAML QUOTING (CRITICAL — SnakeYAML strict parser):\n"
            "- Dotted references in workbook_rules (from/to shorthand) MUST be fully quoted: \"SheetName.ColumnName\"\n"
            "  CORRECT:   from: \"Index.Node\"          to: \"Profile Modification.CRGroup\"\n"
            "  WRONG:     from: Index.Node              to: \"Profile Modification\".CRGroup\n"
            "- Sheet names with spaces MUST be quoted everywhere: \"Profile Modification\"\n"
            "- Column names with spaces MUST be quoted: \"NIAM NAME\", \"IP Address\"\n"
            "- Column names with dots MUST be quoted: \"route.groupName\"\n"
            "- Values containing special chars (:, {, }, [, ], #, &, *, !, |, >, ', \", %) MUST be quoted\n"
            "- Pattern regex values MUST be quoted: pattern: \"^CR[0-9]+$\"\n"
            "- When in doubt, quote the value — extra quotes never break YAML\n\n"

            "FINAL REMINDER — COLUMN NAMES:\n"
            "- Refer back to the MANDATORY column names at the TOP of this prompt\n"
            "- Use EXACT names — do NOT use example names (Node, NIAM NAME, etc.)\n"
            "- Every CIQ column MUST appear in your sheets section\n"
            "- Return ONLY valid YAML — no markdown fences, no explanatory text, no ```yaml blocks"
        )
        system = (
            "You are a Nokia telecom UC configuration expert for the cr-automation framework (ciq-processor). "
            "You generate production-ready validation-rules.yaml files that the ciq-processor JAR consumes "
            "to validate CIQ Excel workbooks. You understand telecom CIQ structure: Index sheets with CRGroup "
            "and Node grouping, IP/Node_Details sheets with NIAM login IDs, and UC-specific data sheets. "
            "Your output must be syntactically valid YAML matching the exact structure used in production."
        )

        response = await call_llm(prompt, system_prompt=system, model=model)
        yaml_content = _extract_yaml(response)
        yaml_content = _fix_yaml_quoting(yaml_content)

        # Validate YAML syntax — return failed status so retry manager re-generates
        try:
            _validate_yaml(yaml_content)
        except Exception as e:
            logger.warning("Generated validation-rules YAML is invalid: %s", e)
            return StepOutput(
                status="failed",
                error={"message": f"Generated YAML has syntax errors: {e}"},
                output_data={"validation_rules_yaml": yaml_content},
            )

        # Auto-fix: correct column/sheet names and strip bad workbook rules
        ciq_path = input_data.get("ciq_file_path", "")
        if ciq_path and Path(ciq_path).exists():
            yaml_content = _autofix_column_names(yaml_content, ciq_path)
            yaml_content = _strip_bad_workbook_rules(yaml_content, ciq_path)

        # Validate column/sheet names against actual CIQ (after auto-fix)
        if ciq_path and Path(ciq_path).exists():
            mismatches = _validate_against_ciq(yaml_content, ciq_path)
            if mismatches:
                error_msg = "Column/sheet name mismatches with CIQ:\n" + "\n".join(mismatches)
                logger.warning("Validation rules mismatch (after auto-fix): %s", error_msg)
                return StepOutput(
                    status="failed",
                    error={"message": error_msg, "mismatches": mismatches},
                    output_data={"validation_rules_yaml": yaml_content},
                )

        return StepOutput(
            status="passed",
            output_data={"validation_rules_yaml": yaml_content},
            artifacts=[],
        )
