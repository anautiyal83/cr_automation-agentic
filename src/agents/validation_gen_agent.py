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
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
                samples = sdata.get("sample_rows", []) if isinstance(sdata, dict) else getattr(sdata, "sample_rows", [])
                ciq_summary_parts.append(f'\nSheet: "{sname}"\n  Columns: {headers}')
                if samples:
                    ciq_summary_parts.append(f"  Sample rows ({len(samples)}):")
                    for r in samples[:3]:
                        vals = {k: v for k, v in (r.items() if isinstance(r, dict) else r) if v}
                        ciq_summary_parts.append(f"    {vals}")
            ir_summary = "\n".join(ciq_summary_parts)
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

        # Real example for few-shot (embedded)
        example = '''# Example: SBC_33_LIC_LOADING_IN_SBC_validation-rules.yaml
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

        prompt = (
            f"Generate a validation-rules.yaml for UC: {uc_name}\n"
            f"Node Type: {node_type}, Activity: {activity}\n\n"

            f"=== CIQ DATA (sheets, headers, sample rows) ===\n{ir_summary}\n\n"

            f"=== USER CONSTRAINTS ===\n{constraints_text or 'None provided'}\n\n"

            f"=== REAL WORKING EXAMPLE ===\n{example}\n\n"

            f"=== SCHEMA REFERENCE (column types, workbook_rules, etc.) ===\n{schema[:8000]}\n\n"

            f"{few_shot_text}{retry_info}\n\n"

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

            "FIXED WORKBOOK_RULES (always include):\n"
            "- Index.Node/NodeGroup MUST be a subset of IP.Node/NodeGroup\n"
            "- If data sheets have a grouping column, add subset/superset linking them to Index\n"
            "- Add any user-specified cross-sheet rules from constraints\n\n"

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

            "ROW RULES (per sheet):\n"
            "- Add unique_key for natural primary keys (e.g. [Node, CRGroup] for Index, [Node, NIAM NAME] for IP)\n"
            "- Add require/forbid with when conditions from constraints\n"
            "- Use one_of: [ColA, ColB] when at least one of multiple columns must be filled\n"
            "- Use all_or_none: [ColA, ColB] when columns must all be filled or all empty\n"
            "- DO NOT use 'at_least_one: true' — that is NOT valid syntax\n\n"

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

            "CRITICAL:\n"
            "- Use EXACT sheet names from the CIQ data — do NOT invent or rename sheets (e.g., if sheet is 'Profile Modification', use that exact name, not 'Data')\n"
            "- Use EXACT column names from the CIQ data — do NOT rename columns\n"
            "- Every column in the CIQ MUST appear in the sheets section\n"
            "- DO NOT add comments like '# Assuming...' — use the actual names from the data\n"
            "- DO NOT generate pattern regex from sample data values — only for known types (IP, email, CRGroup, file paths)\n"
            "- DO NOT generate allowedValues from sample data rows unless explicitly in constraints\n"
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

        # Validate YAML syntax — if invalid, raise so retry logic can re-generate
        try:
            _validate_yaml(yaml_content)
        except Exception as e:
            logger.warning("Generated validation-rules YAML is invalid: %s", e)
            raise ValueError(f"Generated YAML has syntax errors: {e}") from e

        return StepOutput(
            status="passed",
            output_data={"validation_rules_yaml": yaml_content},
            artifacts=[],
        )
