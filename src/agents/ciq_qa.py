"""CIQ QA Test Cycle — generates test cases, test CIQ data, executes, and reports.

Produces a proper QA cycle for Step 5:
1. Parse validation rules → derive test scenarios (positive + negative)
2. Generate modified CIQ Excel files for each negative test
3. Execute each test CIQ against the JAR
4. Compare expected vs actual results
5. Generate test-cases.xlsx + qa-execution-report.xlsx/.html
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Test case data structure ──
# Each TC is a dict with:
#   id, category, scenario, procedure, expected, ciq_file, result_dir,
#   actual, status (PASS/FAIL), remarks


def generate_test_cases(
    rules_yaml_path: str,
    json_output_yaml_path: str | None,
    ciq_path: str,
    uc_name: str,
) -> list[dict[str, Any]]:
    """Analyse validation rules and generate a test case register."""
    from ruamel.yaml import YAML
    from io import StringIO

    yaml = YAML()
    rules = yaml.load(Path(rules_yaml_path).read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        rules = {}

    sheets_def = rules.get("sheets", {}) or {}
    workbook_rules = rules.get("workbook_rules", []) or []

    cases: list[dict[str, Any]] = []
    tc_num = 0

    def _tc_id():
        nonlocal tc_num
        tc_num += 1
        return f"TC{tc_num:02d}"

    # ── Category 1: Positive Tests ──

    cases.append({
        "id": _tc_id(), "category": "Positive",
        "scenario": "Valid CIQ — all mandatory fields present with valid values",
        "procedure": "Run validation with the original unmodified CIQ workbook",
        "expected": "PASS",
        "mutation": None,  # no mutation — use original CIQ
    })

    if json_output_yaml_path and Path(json_output_yaml_path).exists():
        cases.append({
            "id": _tc_id(), "category": "Positive",
            "scenario": "Valid CIQ with JSON output generation",
            "procedure": "Run validation with JSON output config to generate MOP JSON",
            "expected": "PASS",
            "mutation": None,
            "include_json_output": True,
        })

    # ── Category 2: Negative — Missing mandatory columns ──

    for sheet_name, sheet_def in sheets_def.items():
        if not isinstance(sheet_def, dict):
            continue
        columns = sheet_def.get("columns", {}) or {}
        for col_name, col_def in columns.items():
            if not isinstance(col_def, dict):
                continue
            if col_def.get("required", False):
                cases.append({
                    "id": _tc_id(), "category": "Negative - Missing Data",
                    "scenario": f"Mandatory column '{col_name}' cleared in sheet '{sheet_name}'",
                    "procedure": f"Clear all values in column '{col_name}' on sheet '{sheet_name}', run validation",
                    "expected": "FAIL",
                    "mutation": {"type": "clear_column", "sheet": sheet_name, "column": col_name},
                })
                break  # one per sheet to keep test count manageable

    # ── Category 3: Negative — Invalid data types ──

    for sheet_name, sheet_def in sheets_def.items():
        if not isinstance(sheet_def, dict):
            continue
        columns = sheet_def.get("columns", {}) or {}
        for col_name, col_def in columns.items():
            if not isinstance(col_def, dict):
                continue
            col_type = col_def.get("type", "string")
            if col_type == "email":
                cases.append({
                    "id": _tc_id(), "category": "Negative - Invalid Data",
                    "scenario": f"Invalid email in '{col_name}' on sheet '{sheet_name}'",
                    "procedure": f"Replace email values with 'not-an-email' in column '{col_name}'",
                    "expected": "FAIL",
                    "mutation": {"type": "inject_value", "sheet": sheet_name,
                                 "column": col_name, "value": "not-an-email"},
                })
                break
            elif col_type == "ip":
                cases.append({
                    "id": _tc_id(), "category": "Negative - Invalid Data",
                    "scenario": f"Invalid IP address in '{col_name}' on sheet '{sheet_name}'",
                    "procedure": f"Replace IP values with '999.999.999.999' in column '{col_name}'",
                    "expected": "FAIL",
                    "mutation": {"type": "inject_value", "sheet": sheet_name,
                                 "column": col_name, "value": "999.999.999.999"},
                })
                break
            elif col_type == "integer":
                cases.append({
                    "id": _tc_id(), "category": "Negative - Invalid Data",
                    "scenario": f"Non-numeric value in integer column '{col_name}' on sheet '{sheet_name}'",
                    "procedure": f"Replace values with 'abc' in column '{col_name}'",
                    "expected": "FAIL",
                    "mutation": {"type": "inject_value", "sheet": sheet_name,
                                 "column": col_name, "value": "abc"},
                })
                break

    # ── Category 4: Negative — Pattern violations ──

    for sheet_name, sheet_def in sheets_def.items():
        if not isinstance(sheet_def, dict):
            continue
        columns = sheet_def.get("columns", {}) or {}
        for col_name, col_def in columns.items():
            if not isinstance(col_def, dict):
                continue
            if col_def.get("pattern"):
                cases.append({
                    "id": _tc_id(), "category": "Negative - Pattern Violation",
                    "scenario": f"Pattern violation in '{col_name}' on sheet '{sheet_name}'",
                    "procedure": f"Replace values with 'INVALID_PATTERN_VALUE' in column '{col_name}'",
                    "expected": "FAIL",
                    "mutation": {"type": "inject_value", "sheet": sheet_name,
                                 "column": col_name, "value": "INVALID_PATTERN_VALUE"},
                })

    # ── Category 5: Negative — Cross-sheet rule violations ──

    for i, rule in enumerate(workbook_rules):
        if not isinstance(rule, dict):
            continue
        rule_type = None
        rule_def = None
        for rt in ("subset", "superset", "set"):
            if rt in rule:
                rule_type = rt
                rule_def = rule[rt]
                break
        if not rule_type or not rule_def:
            continue

        # Parse the from reference
        from_ref = rule_def.get("from", "")
        if isinstance(from_ref, str) and "." in from_ref:
            from_ref = from_ref.strip('"')
            from_sheet, from_col = from_ref.rsplit(".", 1)
        elif isinstance(from_ref, dict):
            from_sheet = from_ref.get("sheet", "")
            from_col = from_ref.get("column", "")
        else:
            continue

        cases.append({
            "id": _tc_id(), "category": "Negative - Cross-Sheet Violation",
            "scenario": f"Cross-sheet {rule_type} rule violated — inject invalid ref in '{from_sheet}.{from_col}'",
            "procedure": f"Add a row with value 'NONEXISTENT_REF' in '{from_col}' on sheet '{from_sheet}' that doesn't exist in target",
            "expected": "FAIL",
            "mutation": {"type": "inject_row_value", "sheet": from_sheet,
                         "column": from_col, "value": "NONEXISTENT_REF"},
        })
        break  # one cross-sheet test is enough

    # ── Category 6: Positive — Optional fields blank ──

    has_optional = False
    for sheet_name, sheet_def in sheets_def.items():
        if not isinstance(sheet_def, dict):
            continue
        columns = sheet_def.get("columns", {}) or {}
        for col_name, col_def in columns.items():
            if isinstance(col_def, dict) and not col_def.get("required", False):
                has_optional = True
                break
        if has_optional:
            break

    if has_optional:
        cases.append({
            "id": _tc_id(), "category": "Positive",
            "scenario": "Optional fields are blank — only mandatory fields filled",
            "procedure": "Clear all non-required columns across all sheets, run validation",
            "expected": "PASS",
            "mutation": {"type": "clear_optional_columns"},
        })

    return cases


def generate_test_ciq(
    original_ciq: str,
    mutation: dict | None,
    output_path: str,
    rules_yaml_path: str,
) -> None:
    """Create a modified CIQ Excel for a test case based on the mutation spec."""
    import openpyxl

    if mutation is None:
        # Positive test — copy original
        shutil.copy2(original_ciq, output_path)
        return

    wb = openpyxl.load_workbook(original_ciq)
    mut_type = mutation.get("type", "")

    if mut_type == "clear_column":
        sheet = mutation["sheet"]
        column = mutation["column"]
        if sheet in wb.sheetnames:
            ws = wb[sheet]
            col_idx = _find_column_index(ws, column)
            if col_idx is not None:
                for row in range(2, ws.max_row + 1):
                    ws.cell(row=row, column=col_idx).value = None

    elif mut_type == "inject_value":
        sheet = mutation["sheet"]
        column = mutation["column"]
        value = mutation["value"]
        if sheet in wb.sheetnames:
            ws = wb[sheet]
            col_idx = _find_column_index(ws, column)
            if col_idx is not None:
                for row in range(2, ws.max_row + 1):
                    if ws.cell(row=row, column=col_idx).value is not None:
                        ws.cell(row=row, column=col_idx).value = value

    elif mut_type == "inject_row_value":
        sheet = mutation["sheet"]
        column = mutation["column"]
        value = mutation["value"]
        if sheet in wb.sheetnames:
            ws = wb[sheet]
            col_idx = _find_column_index(ws, column)
            if col_idx is not None:
                # Inject value into first data row
                target_row = 2
                ws.cell(row=target_row, column=col_idx).value = value

    elif mut_type == "clear_optional_columns":
        from ruamel.yaml import YAML
        yaml = YAML()
        rules = yaml.load(Path(rules_yaml_path).read_text(encoding="utf-8"))
        sheets_def = rules.get("sheets", {}) or {} if isinstance(rules, dict) else {}
        for sheet_name, sheet_def in sheets_def.items():
            if not isinstance(sheet_def, dict) or sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            columns = sheet_def.get("columns", {}) or {}
            for col_name, col_def in columns.items():
                if isinstance(col_def, dict) and not col_def.get("required", False):
                    col_idx = _find_column_index(ws, col_name)
                    if col_idx is not None:
                        for row in range(2, ws.max_row + 1):
                            ws.cell(row=row, column=col_idx).value = None

    wb.save(output_path)


def _find_column_index(ws, column_name: str) -> int | None:
    """Find 1-based column index by header name (case-insensitive)."""
    target = column_name.strip().lower()
    for col in range(1, ws.max_column + 1):
        header = ws.cell(row=1, column=col).value
        if header and str(header).strip().lower() == target:
            return col
    return None


async def execute_test_cases(
    test_cases: list[dict],
    original_ciq: str,
    rules_path: str,
    json_output_path: str | None,
    ciq_jar: str,
    node_type: str,
    activity: str,
    qa_dir: str,
    timeout: int = 300,
) -> list[dict]:
    """Execute all test cases and populate results."""
    test_data_dir = Path(qa_dir) / "test_data"
    results_dir = Path(qa_dir) / "results"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    for tc in test_cases:
        tc_id = tc["id"]
        mutation = tc.get("mutation")
        include_json = tc.get("include_json_output", False)

        # Generate test CIQ
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', tc["scenario"])[:60]
        ciq_filename = f"{tc_id}_{safe_name}.xlsx"
        ciq_file = str(test_data_dir / ciq_filename)
        tc["ciq_file"] = ciq_file

        try:
            generate_test_ciq(original_ciq, mutation, ciq_file, rules_path)
        except Exception as e:
            tc["actual"] = "ERROR"
            tc["status"] = "BLOCKED"
            tc["remarks"] = f"Failed to generate test CIQ: {e}"
            continue

        # Create result dir for this TC
        tc_result_dir = str(results_dir / tc_id)
        Path(tc_result_dir).mkdir(exist_ok=True)
        tc["result_dir"] = tc_result_dir

        # Build JAR command
        cmd = [
            "java", "-jar", ciq_jar,
            "--mode", "ciq-validate",
            "--ciq", ciq_file,
            "--node-type", node_type,
            "--activity", activity,
            "--rules", rules_path,
            "--output", tc_result_dir,
        ]
        if include_json and json_output_path and Path(json_output_path).exists():
            cmd.extend([
                "--json-output-config-file", json_output_path,
                "--json-output-dir", tc_result_dir,
            ])

        # Execute
        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True, timeout=timeout,
            )
            jar_passed = result.returncode == 0
            tc["actual"] = "PASS" if jar_passed else "FAIL"
            tc["returncode"] = result.returncode
            tc["stdout"] = result.stdout
            tc["stderr"] = result.stderr

            # Compare expected vs actual
            expected = tc["expected"]
            if expected == "PASS" and jar_passed:
                tc["status"] = "PASS"
                tc["remarks"] = "Validation passed as expected"
            elif expected == "FAIL" and not jar_passed:
                tc["status"] = "PASS"
                tc["remarks"] = "Validation correctly rejected invalid data"
            elif expected == "PASS" and not jar_passed:
                tc["status"] = "FAIL"
                tc["remarks"] = f"Expected PASS but got FAIL: {(result.stderr or result.stdout)[:200]}"
            elif expected == "FAIL" and jar_passed:
                tc["status"] = "FAIL"
                tc["remarks"] = "Expected FAIL but validation passed — rule may not be enforced"
            else:
                tc["status"] = "FAIL"
                tc["remarks"] = "Unexpected result"

        except subprocess.TimeoutExpired:
            tc["actual"] = "TIMEOUT"
            tc["status"] = "BLOCKED"
            tc["remarks"] = f"JAR timed out after {timeout}s"
        except Exception as e:
            tc["actual"] = "ERROR"
            tc["status"] = "BLOCKED"
            tc["remarks"] = str(e)

    return test_cases


# ── Report Generation ──

def generate_test_case_register(filepath: str, test_cases: list[dict], uc_name: str) -> None:
    """Generate test-cases.xlsx — the test case register."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        logger.warning("openpyxl not available")
        return

    wb = Workbook()
    thin = Side(style="thin", color="CCCCCC")
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    hdr_font = Font(bold=True, size=11, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1A3A5C")
    cat_font = Font(bold=True, size=10, color="1A3A5C")
    cat_fill = PatternFill("solid", fgColor="E8EDF3")

    ws = wb.active
    ws.title = "Test Cases"

    # Title
    ws.merge_cells("A1:F1")
    ws["A1"] = f"CIQ Validation Test Case Register — {uc_name}"
    ws["A1"].font = Font(bold=True, size=14, color="1A3A5C")
    ws["A2"] = f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    ws["A2"].font = Font(size=10, color="888888")
    ws.append([])

    # Headers
    headers = ["Test ID", "Category", "Test Scenario", "Test Procedure", "Expected Result"]
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    # Group by category
    current_cat = None
    for tc in test_cases:
        cat = tc.get("category", "")
        if cat != current_cat:
            current_cat = cat
            ws.append([cat])
            r = ws.max_row
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
            ws.cell(r, 1).font = cat_font
            ws.cell(r, 1).fill = cat_fill

        row = [
            tc["id"],
            tc["category"],
            tc["scenario"],
            tc["procedure"],
            tc["expected"],
        ]
        ws.append(row)
        r = ws.max_row
        for c in range(1, 6):
            ws.cell(r, c).border = border
            ws.cell(r, c).alignment = Alignment(vertical="top", wrap_text=True)
        # Color expected
        exp_cell = ws.cell(r, 5)
        exp_cell.font = Font(bold=True, color="27AE60" if tc["expected"] == "PASS" else "E74C3C")
        exp_cell.alignment = Alignment(horizontal="center")

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 50
    ws.column_dimensions["D"].width = 55
    ws.column_dimensions["E"].width = 12

    wb.save(filepath)


def generate_execution_report_excel(filepath: str, test_cases: list[dict], uc_name: str) -> None:
    """Generate qa-execution-report.xlsx."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        logger.warning("openpyxl not available")
        return

    wb = Workbook()
    thin = Side(style="thin", color="CCCCCC")
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    hdr_font = Font(bold=True, size=11, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1A3A5C")
    pass_fill = PatternFill("solid", fgColor="EAFAF1")
    fail_fill = PatternFill("solid", fgColor="FDF2F2")
    blocked_fill = PatternFill("solid", fgColor="FFF8E1")
    pass_font = Font(bold=True, color="27AE60")
    fail_font = Font(bold=True, color="E74C3C")
    blocked_font = Font(bold=True, color="F57F17")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Summary sheet ──
    ws = wb.active
    ws.title = "Summary"
    total = len(test_cases)
    passed = sum(1 for tc in test_cases if tc.get("status") == "PASS")
    failed = sum(1 for tc in test_cases if tc.get("status") == "FAIL")
    blocked = total - passed - failed

    ws.merge_cells("A1:F1")
    ws["A1"] = f"QA Execution Report — {uc_name}"
    ws["A1"].font = Font(bold=True, size=14, color="1A3A5C")
    ws["A2"] = f"Executed: {ts}"
    ws["A2"].font = Font(size=10, color="888888")
    ws.append([])
    ws.append(["Total Test Cases", total])
    ws.append(["Passed", passed])
    ws.append(["Failed", failed])
    ws.append(["Blocked", blocked])
    cov = (passed / total * 100) if total else 0
    ws.append(["Pass Rate", f"{cov:.1f}%"])
    ws.cell(ws.max_row, 2).font = pass_font if cov == 100 else fail_font
    for r in range(4, ws.max_row + 1):
        ws.cell(r, 1).font = Font(bold=True)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 15

    # ── Execution Details sheet ──
    ws2 = wb.create_sheet("Execution Details")
    headers = ["Test ID", "Scenario", "Expected", "Actual", "Status", "Remarks"]
    ws2.append(headers)
    for cell in ws2[1]:
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    for tc in test_cases:
        st = tc.get("status", "BLOCKED")
        row = [
            tc["id"],
            tc["scenario"],
            tc.get("expected", ""),
            tc.get("actual", ""),
            st,
            tc.get("remarks", ""),
        ]
        ws2.append(row)
        r = ws2.max_row
        fill = pass_fill if st == "PASS" else fail_fill if st == "FAIL" else blocked_fill
        font = pass_font if st == "PASS" else fail_font if st == "FAIL" else blocked_font
        for c in range(1, 7):
            ws2.cell(r, c).fill = fill
            ws2.cell(r, c).border = border
            ws2.cell(r, c).alignment = Alignment(vertical="top", wrap_text=True)
        ws2.cell(r, 5).font = font
        ws2.cell(r, 5).alignment = Alignment(horizontal="center")

    ws2.column_dimensions["A"].width = 8
    ws2.column_dimensions["B"].width = 50
    ws2.column_dimensions["C"].width = 10
    ws2.column_dimensions["D"].width = 10
    ws2.column_dimensions["E"].width = 10
    ws2.column_dimensions["F"].width = 60

    wb.save(filepath)


def generate_execution_report_html(filepath: str, test_cases: list[dict], uc_name: str,
                                    node_type: str, activity: str) -> None:
    """Generate qa-execution-report.html for milestone review."""
    import html as html_mod

    total = len(test_cases)
    passed = sum(1 for tc in test_cases if tc.get("status") == "PASS")
    failed = sum(1 for tc in test_cases if tc.get("status") == "FAIL")
    blocked = total - passed - failed
    overall = "PASSED" if failed == 0 and blocked == 0 and total > 0 else "FAILED"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pct = f"{passed / total * 100:.1f}" if total else "0"

    # Build table rows
    rows_html = ""
    for tc in test_cases:
        st = tc.get("status", "BLOCKED")
        st_cls = "pass" if st == "PASS" else "fail" if st == "FAIL" else "blocked"
        actual = tc.get("actual", "—")
        actual_cls = "pass" if actual == "PASS" else "fail" if actual == "FAIL" else ""
        exp = tc.get("expected", "")
        exp_cls = "pass" if exp == "PASS" else "fail"
        rows_html += f"""<tr class="{st_cls}-row">
          <td class="tc-id">{html_mod.escape(tc['id'])}</td>
          <td class="tc-cat">{html_mod.escape(tc.get('category',''))}</td>
          <td>{html_mod.escape(tc['scenario'])}</td>
          <td class="{exp_cls}">{exp}</td>
          <td class="{actual_cls}">{actual}</td>
          <td class="{st_cls}">{st}</td>
          <td class="remarks">{html_mod.escape(tc.get('remarks',''))}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QA Execution Report — {html_mod.escape(uc_name)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #333; font-size: 13px; }}
.header {{ background: #1a3a5c; color: #fff; padding: 20px 30px; }}
.header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
.header .meta {{ font-size: 12px; color: #b0c4d8; display: flex; gap: 24px; flex-wrap: wrap; }}
.summary {{ display: flex; align-items: center; gap: 24px; padding: 16px 30px; margin: 16px 24px;
  border-radius: 8px; border-left: 6px solid; }}
.summary.pass {{ background: #eafaf1; border-color: #27ae60; }}
.summary.fail {{ background: #fdf2f2; border-color: #e74c3c; }}
.summary .overall {{ font-size: 28px; font-weight: 700; }}
.summary.pass .overall {{ color: #27ae60; }}
.summary.fail .overall {{ color: #e74c3c; }}
.stats {{ display: flex; gap: 20px; }}
.stat {{ text-align: center; min-width: 60px; }}
.stat-num {{ font-size: 20px; font-weight: 700; color: #1a3a5c; }}
.stat-num.green {{ color: #27ae60; }} .stat-num.red {{ color: #e74c3c; }} .stat-num.amber {{ color: #f57f17; }}
.stat-label {{ font-size: 10px; text-transform: uppercase; color: #888; }}
table {{ width: calc(100% - 48px); margin: 0 24px 16px; border-collapse: collapse; background: #fff;
  border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
th {{ background: #1a3a5c; color: #fff; padding: 10px 12px; text-align: left; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #eef0f3; vertical-align: top; }}
.tc-id {{ font-weight: 700; white-space: nowrap; color: #1a3a5c; }}
.tc-cat {{ font-size: 11px; color: #666; white-space: nowrap; }}
.pass {{ color: #27ae60; font-weight: 700; }}
.fail {{ color: #e74c3c; font-weight: 700; }}
.blocked {{ color: #f57f17; font-weight: 700; }}
.remarks {{ font-size: 11px; color: #666; }}
.pass-row td {{ background: #fafff8; }}
.fail-row td {{ background: #fef9f9; }}
.blocked-row td {{ background: #fffdf0; }}
.footer {{ padding: 12px 30px; font-size: 11px; color: #999; text-align: center; }}
</style></head>
<body>
<div class="header">
  <h1>QA Execution Report</h1>
  <div class="meta">
    <span><b>UC:</b> {html_mod.escape(uc_name)}</span>
    <span><b>Node Type:</b> {html_mod.escape(node_type)}</span>
    <span><b>Activity:</b> {html_mod.escape(activity)}</span>
    <span><b>Executed:</b> {ts}</span>
  </div>
</div>
<div class="summary {'pass' if overall == 'PASSED' else 'fail'}">
  <div class="overall">{overall}</div>
  <div class="stats">
    <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Total</div></div>
    <div class="stat"><div class="stat-num green">{passed}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-num red">{failed}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-num amber">{blocked}</div><div class="stat-label">Blocked</div></div>
    <div class="stat"><div class="stat-num">{pct}%</div><div class="stat-label">Pass Rate</div></div>
  </div>
</div>
<table>
  <thead><tr>
    <th>Test ID</th><th>Category</th><th>Scenario</th>
    <th>Expected</th><th>Actual</th><th>Status</th><th>Remarks</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<div class="footer">Generated by CR Automation Pipeline — Step 5: CIQ QA Cycle</div>
</body></html>"""

    Path(filepath).write_text(html, encoding="utf-8")
