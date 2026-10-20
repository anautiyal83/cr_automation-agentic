"""Step 4: JSON template YAML generation."""
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


class JsonTemplateAgent:
    step_number = 4
    step_name = "JSON Template Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(4, config)

        schema_path = Path("schemas/json-output-schema.yaml")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

        prompt_path = Path("src/prompts/json_template_gen.yaml")
        prompt_template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        few_shot_text = ""
        if few_shot_examples:
            for ex in few_shot_examples[:3]:
                if "json_template" in ex:
                    few_shot_text += (
                        f"\n--- Example from UC '{ex['uc_name']}' ---\n"
                        f"{ex['json_template']}\n"
                    )

        # Build per-sheet CIQ summary
        ciq_data = input_data.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})
        sheet_names = ciq_data.get("sheet_names", [])

        if per_sheet:
            ciq_parts = [f"CIQ Sheets: {sheet_names}\n"]
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
                samples = sdata.get("sample_rows", []) if isinstance(sdata, dict) else getattr(sdata, "sample_rows", [])
                ciq_parts.append(f'\nSheet: "{sname}"\n  Columns: {headers}')
                if samples:
                    for r in samples[:2]:
                        vals = {k: v for k, v in (r.items() if isinstance(r, dict) else r) if v}
                        ciq_parts.append(f"    {vals}")
            ir_summary = "\n".join(ciq_parts)
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

        # Include validation rules from Step 3 for exact column name reference
        val_rules = input_data.get("artifacts", {}).get("validation_rules_yaml", "")
        val_rules_section = ""
        if val_rules:
            val_rules_section = (
                f"\n=== VALIDATION RULES (from Step 3 — use these EXACT column names) ===\n"
                f"{val_rules[:4000]}\n"
            )

        uc_name = input_data.get("uc_name", "UC")
        parts = uc_name.split("_", 1)
        node_type = parts[0] if len(parts) == 2 else uc_name
        activity = parts[1] if len(parts) == 2 else ""

        # Real working examples embedded
        example_simple = '''# Example: Simple UC (no data tables)
output_mode: single
data:
  schemaVersion: "2"
  meta:
    nodeType: MRF
    activity: ANNOUNCEMENT_LOADING
  crGroups:
    _each: "DISTINCT Index.CRGroup AS $cr"
    crGroup: $cr
    email: "Index.Email WHERE Index.CRGroup = $cr"
    nodeGroups:
      _each: "DISTINCT Index.Node WHERE Index.CRGroup = $cr AS $node"
      nodeGroup: $node
      niamID: "Node_Details.'NIAM NAME' WHERE Node_Details.Node_Name = $node"
      configData:
        _each: "ANNOUNCEMENT_FILES WHERE GROUP IN Index.GROUP"
        INPUT_FILE: INPUT_FILE
        MRF_DESTINATION_PATH: MRF_DESTINATION_PATH'''

        example_complex = '''# Example: Complex UC (with data tables and configSequences)
output_mode: single
data:
  schemaVersion: "2"
  meta:
    nodeType: SBC
    activity: 147_IP_POI_CONFIG_ISBC
    tableKeys:
      CFEDPortTable:
        recordPrimaryKeys: "CFED_ID,APPLICATION_TYPE,APPLICATION_GROUP"
        writeOnlyFields: ["MW_INTERFACE_ALLOWED_SIP_HEADERS_LIST"]
      CRFTargetList:
        recordPrimaryKeys: "TARGET_LIST_ID"
        subrecordKeys:
          CRFTargetListEntry: "TARGET_ID,VN_ID,TG_ID"
  crGroups:
    _each: "DISTINCT Index.CRGroup AS $cr"
    crGroup: $cr
    email: "Index.Email WHERE Index.CRGroup = $cr"
    nodeGroups:
      _each: "DISTINCT Index.NodeGroup WHERE Index.CRGroup = $cr AS $ng"
      nodeGroup: $ng
      nodes:
        _each: "IP WHERE IP.NodeGroup = $ng"
        node:   IP.Node
        niamID: "IP.'NIAM NAME'"
      configSequences:
        _each: "DISTINCT Index.CONFIG_SEQ WHERE Index.NodeGroup = $ng AS $seq"
        configSeq: $seq
        tables:
          _each: "DISTINCT Index.TABLES WHERE Index.NodeGroup = $ng AND Index.CONFIG_SEQ = $seq AS $table"
          table:    $table
          category: "Index.CATEGORY WHERE Index.NodeGroup = $ng AND Index.CONFIG_SEQ = $seq AND Index.TABLES = $table"
          records:
            _each: "$table WHERE NodeGroup = $ng AND CONFIG_SEQ = $seq"
            data:
              _row: "*"'''

        prompt = (
            f"Generate a json-output.yaml for UC: {uc_name}\n"
            f"Node Type: {node_type}, Activity: {activity}\n\n"

            f"=== CIQ DATA ===\n{ir_summary}\n\n"

            f"=== REAL WORKING EXAMPLES ===\n\n{example_simple}\n\n{example_complex}\n\n"

            f"=== SCHEMA REFERENCE ===\n{schema[:8000]}\n\n"

            f"{few_shot_text}{retry_info}{val_rules_section}\n\n"

            "=== GENERATION INSTRUCTIONS ===\n\n"

            "Generate a json-output.yaml following the EXACT structure used in production.\n\n"

            "TOP-LEVEL STRUCTURE (MANDATORY):\n"
            "```\n"
            "output_mode: single\n"
            "data:\n"
            "  schemaVersion: \"2\"\n"
            "  meta:\n"
            "    nodeType: <NODE_TYPE>\n"
            "    activity: <ACTIVITY>\n"
            "    tableKeys:          # optional — only if UC has data tables\n"
            "  crGroups:             # TOP LEVEL — iterate by CRGroup\n"
            "    _each: \"DISTINCT <Index>.<CRGroupCol> AS $cr\"\n"
            "    crGroup: $cr\n"
            "    email: \"<Index>.Email WHERE <Index>.<CRGroupCol> = $cr\"\n"
            "    nodeGroups:         # SECOND LEVEL — iterate by NodeGroup/Zone within CR\n"
            "      _each: \"DISTINCT <Index>.<GroupCol> WHERE <Index>.<CRGroupCol> = $cr AS $ng\"\n"
            "      nodeGroup: $ng\n"
            "      nodes: ...        # IP/Node details\n"
            "      configSequences/tables/configData: ...  # UC data\n"
            "```\n\n"

            "KEY RULES:\n"
            "1. output_mode: always 'single' unless user specifies individual\n"
            "2. data.schemaVersion: always \"2\"\n"
            "3. meta.nodeType and meta.activity: from UC name\n"
            "4. meta.tableKeys: ONLY if CIQ has data tables referenced from Index.Tables — "
            "each table entry has recordPrimaryKeys (comma-separated column names that form the unique key). "
            "Optionally subrecordKeys, writeOnlyFields, immutableFields\n"
            "5. HIERARCHY: crGroups → nodeGroups → nodes → configData/tables\n"
            "   - crGroups: ALWAYS top level, iterate DISTINCT CRGroup from Index\n"
            "   - crGroup: the CR iteration variable\n"
            "   - email: lookup from Index filtered by CRGroup\n"
            "   - nodeGroups: nested under crGroups, iterate DISTINCT Node/NodeGroup/Zone from Index filtered by CRGroup\n"
            "   - nodeGroup: the group iteration variable\n"
            "6. nodes: under nodeGroups, iterate IP/Node_Details sheet filtered by group\n"
            "   - node: the node name column\n"
            "   - niamID: lookup NIAM NAME (quote if has space: \"IP.'NIAM NAME'\")\n"
            "   - If multiple NIAM IDs (NIAM_NAME1, NIAM_NAME2, etc.), list each as niamID-1, niamID-2\n"
            "7. For UCs with data tables (Index has Tables/CONFIG_SEQ columns):\n"
            "   - If Index has CONFIG_SEQ: use configSequences with nested tables\n"
            "   - If Index has Tables: iterate DISTINCT tables\n"
            "   - Each table's records: _each: \"$table WHERE <GroupCol> = $ng\"\n"
            "   - Record data: _row: \"*\" (emit all columns as key-value map)\n"
            "8. For simple UCs (no Tables column in Index):\n"
            "   - Put configData directly under nodeGroups\n"
            "   - _each: iterate data sheet with WHERE filter by group\n"
            "   - List specific column names from the data sheet\n\n"

            "DIRECTIVE REFERENCE:\n"
            "- _each: \"DISTINCT Sheet.Col AS $var\" — iterate distinct values\n"
            "- _each: \"Sheet WHERE Col = $var\" — iterate filtered rows\n"
            "- _each: \"$table WHERE Col = $var\" — iterate table by variable name\n"
            "- _each: \"Sheet WHERE Col IN Other.Col\" — membership filter\n"
            "- _row: \"*\" — emit all columns as key-value map\n"
            "- _join: \"Sheet.Col\" with separator — join distinct values\n"
            "- Column names with spaces MUST be single-quoted: \"IP.'NIAM NAME'\"\n"
            "- WHERE conditions: Sheet.Col = $var AND Sheet.Col2 = $var2\n\n"

            "CRITICAL:\n"
            "- Use EXACT sheet names and column names from the CIQ data\n"
            "- Column names with spaces use single quotes inside double quotes: \"IP.'NIAM NAME'\"\n"
            "- The grouping column (Node/NodeGroup/Zone) must match between Index and IP sheets\n"
            "- Return ONLY valid YAML — no markdown fences, no explanatory text"
        )
        system = (
            "You are a Nokia telecom UC configuration expert for the cr-automation framework. "
            "You generate production-ready json-output.yaml files consumed by ciq-processor, "
            "mop-generator-utility, and network-command-executor-utility. "
            "You understand CIQ structure: Index sheet with grouping/CR/email, IP sheet with node details, "
            "and UC-specific data tables. Use _each, _row, _join, WHERE directives correctly."
        )

        response = await call_llm(prompt, system_prompt=system, model=model)
        yaml_content = _extract_yaml(response)

        # Fix common YAML issues
        yaml_content = _fix_yaml_quoting(yaml_content)
        yaml_content = _fix_yaml_syntax(yaml_content)

        # Auto-fix column references to match actual CIQ
        ciq_path = input_data.get("ciq_file_path", "")
        logger.info("Step 4: ciq_path=%s exists=%s", ciq_path, Path(ciq_path).exists() if ciq_path else False)
        if ciq_path and Path(ciq_path).exists():
            yaml_content = _autofix_json_template(yaml_content, ciq_path)
            logger.info("Step 4: auto-fix applied")

        # Validate YAML syntax — return failed so retry can re-generate
        try:
            _validate_yaml(yaml_content)
        except Exception as e:
            logger.warning("Generated json-output YAML is invalid: %s", e)
            return StepOutput(
                status="failed",
                error={"message": f"Generated YAML has syntax errors: {e}"},
                output_data={"json_template_yaml": yaml_content},
            )

        return StepOutput(
            status="passed",
            output_data={"json_template_yaml": yaml_content},
            artifacts=[],
        )


def _autofix_json_template(yaml_text: str, ciq_path: str) -> str:
    """Auto-fix column and sheet references in json-output.yaml to match actual CIQ.

    Scans all string values in the YAML for Sheet.Column references and fixes them
    using fuzzy matching against actual CIQ column names.
    """
    import openpyxl
    from difflib import get_close_matches

    try:
        wb = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
    except Exception:
        return yaml_text

    # Build CIQ structure
    ciq_sheets: dict[str, list[str]] = {}
    ciq_sheet_map: dict[str, str] = {}  # lower → original
    all_columns: dict[str, str] = {}  # col_lower → col_original (across all sheets)
    for sn in wb.sheetnames:
        ws = wb[sn]
        headers = []
        for cell in next(ws.iter_rows(min_row=1, max_row=1), []):
            if cell.value:
                h = str(cell.value).strip()
                headers.append(h)
                all_columns[h.lower()] = h
        ciq_sheets[sn] = headers
        ciq_sheet_map[sn.lower()] = sn
    wb.close()

    all_col_names_lower = list(all_columns.keys())

    def _fix_col_ref(col_name: str, sheet_name: str = "") -> str:
        """Fix a single column reference, optionally within a specific sheet."""
        cl = col_name.lower().strip("'\"")
        # Try sheet-specific lookup first
        if sheet_name:
            actual_sn = ciq_sheet_map.get(sheet_name.lower(), "")
            sheet_cols = ciq_sheets.get(actual_sn, [])
            sheet_cols_lower = {c.lower(): c for c in sheet_cols}
            if cl in sheet_cols_lower:
                return sheet_cols_lower[cl]
            matches = get_close_matches(cl, list(sheet_cols_lower.keys()), n=1, cutoff=0.5)
            if matches:
                fixed = sheet_cols_lower[matches[0]]
                logger.info("Auto-fix json-output: '%s' -> '%s' (in sheet '%s')", col_name, fixed, actual_sn)
                return fixed
        # Fallback to global lookup
        if cl in all_columns:
            return all_columns[cl]
        matches = get_close_matches(cl, all_col_names_lower, n=1, cutoff=0.6)
        if matches:
            fixed = all_columns[matches[0]]
            logger.info("Auto-fix json-output: '%s' -> '%s'", col_name, fixed)
            return fixed
        return col_name

    def _fix_sheet_ref(sheet_name: str) -> str:
        """Fix a sheet name reference."""
        sl = sheet_name.lower()
        if sl in ciq_sheet_map:
            return ciq_sheet_map[sl]
        return sheet_name

    # Parse YAML and fix references in the data structure
    from ruamel.yaml import YAML
    from io import StringIO
    import re

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        data = yaml.load(StringIO(yaml_text))
    except Exception:
        return yaml_text

    if not isinstance(data, dict):
        return yaml_text

    changed = False

    # Identify the data sheet (not Index, not IP)
    data_sheets = [sn for sn in ciq_sheets if sn.lower() not in ("index", "ip")]
    data_sheet = data_sheets[0] if data_sheets else ""

    # Identify the grouping column (first column in Index sheet)
    index_cols = ciq_sheets.get(ciq_sheet_map.get("index", "Index"), [])
    grouping_col = index_cols[0] if index_cols else "ZONE"

    # ── STRUCTURAL FIXES ──
    # The json-output YAML has a nested dict structure (not lists):
    # data.crGroups.nodeGroups.nodes (each level is a dict with _each, and child keys)
    def _fix_structure(obj: dict, path: str = ""):
        """Recursively fix structural issues in the json-output template."""
        nonlocal changed
        if not isinstance(obj, dict):
            return

        # Fix niamID-N or niamIDN → single niamID (at any level that has nodes)
        niam_keys = [k for k in obj if k != "niamID" and (k.startswith("niamID-") or (k.startswith("niamID") and k[-1].isdigit()))]
        if niam_keys:
            if "niamID" not in obj:
                # Use first NIAM column
                first_val = obj[niam_keys[0]]
                obj["niamID"] = first_val
                logger.info("Auto-fix json-output: merged %s -> niamID = %s", niam_keys, first_val)
            for k in niam_keys:
                del obj[k]
            changed = True

        # Fix configData → tables structure
        if "configData" in obj and "tables" not in obj:
            obj.pop("configData")
            # Build proper tables structure
            ng_var = "$ng"
            # Find the iteration variable from the _each of this level
            each_val = obj.get("_each", "")
            if isinstance(each_val, str) and " AS " in each_val:
                ng_var = each_val.split(" AS ")[-1].strip()
            obj["tables"] = {
                "_each": f'"{data_sheet}"' if data_sheet else '"data"',
                "table": data_sheet or "data",
                "records": {
                    "_each": f'"{data_sheet} WHERE {grouping_col} = {ng_var}"' if data_sheet else '"data"',
                    "data": {"_row": "*"},
                },
            }
            changed = True
            logger.info("Auto-fix json-output: converted configData to tables structure")

        # Recurse into child dicts
        for v in obj.values():
            if isinstance(v, dict):
                _fix_structure(v)

    data_section = data.get("data", {})
    if isinstance(data_section, dict):
        _fix_structure(data_section)

    # ── STRUCTURAL FIX 3: Ensure tableKeys exists ──
    meta = data.get("data", {}).get("meta", {})
    if isinstance(meta, dict) and "tableKeys" not in meta and data_sheet:
        # Build tableKeys from data sheet columns
        ds_cols = ciq_sheets.get(data_sheet, [])
        # Use first 2 columns as primary keys
        pk_cols = ds_cols[:2] if len(ds_cols) >= 2 else ds_cols[:1] if ds_cols else ["id"]
        meta["tableKeys"] = {
            data_sheet: {
                "recordPrimaryKeys": ",".join(pk_cols),
            }
        }
        changed = True
        logger.info("Auto-fix json-output: added tableKeys for '%s'", data_sheet)

    def _fix_string_value(val: str) -> str:
        """Fix column/sheet references in a string value."""
        nonlocal changed
        if not isinstance(val, str):
            return val
        result = val

        # Fix Sheet.Column patterns: Index.Node → Index.ZONE
        def _fix_dotted(m):
            nonlocal changed
            sheet = m.group(1)
            col = m.group(2).strip("'\"")
            new_sheet = _fix_sheet_ref(sheet)
            new_col = _fix_col_ref(col, sheet_name=new_sheet)
            if new_sheet != sheet or new_col != col:
                changed = True
            col_ref = f"'{new_col}'" if " " in new_col else new_col
            return f"{new_sheet}.{col_ref}"

        result = re.sub(r"([A-Z]\w*)\.([\w]+|'[^']*')", _fix_dotted, result)

        # Fix WHERE/AND column references
        def _fix_where(m):
            nonlocal changed
            prefix = m.group(1)
            col = m.group(2)
            new_col = _fix_col_ref(col)
            if new_col != col:
                changed = True
            return f"{prefix}{new_col}"

        result = re.sub(r"(WHERE\s+|AND\s+)(\w+)(?=\s*=)", _fix_where, result)

        return result

    def _fix_recursive(obj):
        """Recursively fix all string values in a nested dict/list."""
        nonlocal changed
        if isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                new_key = k
                # Fix tableKeys keys — should be actual CIQ sheet names
                if isinstance(v, dict) and k not in ("meta", "data", "crGroups", "nodeGroups", "nodes", "tables", "records"):
                    # Only rename keys inside tableKeys section
                    parent_is_tablekeys = False  # will be handled separately below
                    fixed_key = k
                    if fixed_key != k:
                        new_key = fixed_key
                        changed = True
                        logger.info("Auto-fix json-output: tableKey '%s' -> '%s'", k, fixed_key)
                new_dict[new_key] = _fix_recursive(v)
            return new_dict
        elif isinstance(obj, list):
            return [_fix_recursive(item) for item in obj]
        elif isinstance(obj, str):
            return _fix_string_value(obj)
        return obj

    fixed_data = _fix_recursive(data)

    # Fix tableKeys section — rename keys to actual CIQ data sheet names
    meta = fixed_data.get("data", {}).get("meta", {})
    table_keys = meta.get("tableKeys", {})
    if isinstance(table_keys, dict):
        data_sheets = [sn for sn in ciq_sheets if sn.lower() not in ("index", "ip")]
        new_table_keys = {}
        for tk_name, tk_def in table_keys.items():
            # Map tableKey name to actual CIQ data sheet
            new_name = _fix_sheet_ref(tk_name)
            if new_name == tk_name and data_sheets:
                new_name = data_sheets[0]
                logger.info("Auto-fix json-output: tableKey '%s' -> '%s'", tk_name, new_name)
                changed = True
            elif new_name != tk_name:
                changed = True
            # Fix recordPrimaryKeys
            if isinstance(tk_def, dict) and "recordPrimaryKeys" in tk_def:
                rpk = tk_def["recordPrimaryKeys"]
                if isinstance(rpk, str):
                    # Use columns from the target data sheet
                    sheet_cols = ciq_sheets.get(new_name, [])
                    new_rpk = ",".join(_fix_col_ref(k.strip(), sheet_name=new_name) for k in rpk.split(","))
                    if new_rpk != rpk:
                        tk_def["recordPrimaryKeys"] = new_rpk
                        changed = True
            new_table_keys[new_name] = tk_def
        meta["tableKeys"] = new_table_keys

    if changed:
        buf = StringIO()
        yaml_out = YAML()
        yaml_out.default_flow_style = False
        yaml_out.dump(fixed_data, buf)
        logger.info("Auto-fix applied to json-output.yaml")
        return buf.getvalue()

    return yaml_text


def _fix_yaml_syntax(yaml_text: str) -> str:
    """Fix common YAML syntax issues in LLM-generated json-output templates."""
    # Strip control characters
    yaml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', yaml_text)
    # Fix tabs → spaces
    yaml_text = yaml_text.replace("\t", "  ")
    # Strip trailing whitespace
    yaml_text = "\n".join(line.rstrip() for line in yaml_text.split("\n"))

    # Fix nested quotes: value containing unescaped quotes
    # e.g., _each: "Profile Modification WHERE "Profile Modification.Zone" = $zone"
    # → _each: 'Profile Modification WHERE Profile Modification.Zone = $zone'
    def _fix_line(line: str) -> str:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            return line
        # Match key: "value with "nested" quotes"
        m = re.match(r'^(\s*[\w_-]+:\s+)"(.+)"(\s*)$', line)
        if m:
            indent = m.group(1)
            content = m.group(2)
            trail = m.group(3)
            # Check for nested double quotes (not escaped)
            inner = content[:-1] if content.endswith('"') else content
            if '"' in inner:
                # Switch to single quotes, remove inner double quotes
                clean = content.replace('"', '')
                return f"{indent}'{clean}'{trail}"
        return line

    yaml_text = "\n".join(_fix_line(l) for l in yaml_text.split("\n"))
    return yaml_text


def _fix_yaml_quoting(yaml_text: str) -> str:
    """Fix common YAML quoting issues for SnakeYAML.

    Fixes single-quoted dotted refs: 'Sheet Name'.'Column' → "Sheet Name.Column"
    """
    # Fix 'Sheet Name'.'Column Name' → "Sheet Name.Column Name"
    yaml_text = re.sub(
        r"'([^']+)'\.('([^']+)'|(\S+))",
        lambda m: f'"{m.group(1)}.{m.group(3) or m.group(4)}"',
        yaml_text,
    )
    # Fix "Sheet Name"."Column Name" → "Sheet Name.Column Name"
    yaml_text = re.sub(
        r'"([^"]+)"\."([^"]+)"',
        r'"\1.\2"',
        yaml_text,
    )
    # Fix "Sheet Name".Column → "Sheet Name.Column"
    yaml_text = re.sub(
        r'"([^"]+)"\.(\S+)',
        r'"\1.\2"',
        yaml_text,
    )
    return yaml_text


def _validate_yaml(yaml_text: str) -> str:
    """Validate YAML syntax."""
    from ruamel.yaml import YAML
    from io import StringIO
    yaml = YAML()
    yaml.load(StringIO(yaml_text))
    return yaml_text
