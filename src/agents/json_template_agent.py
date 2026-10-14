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

            f"{few_shot_text}{retry_info}\n\n"

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

        return StepOutput(
            status="passed",
            output_data={"json_template_yaml": yaml_content},
            artifacts=[],
        )
