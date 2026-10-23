"""Step 6: Workflow YAML generation — generates cli-automation workflow from MOP + CIQ data."""
from __future__ import annotations

import json
import logging
import re
from io import StringIO
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


def _extract_yaml(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    match = re.search(r'```(?:yaml)?\s*\n(.*?)```', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _validate_yaml(yaml_text: str) -> str:
    """Validate YAML syntax."""
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.load(StringIO(yaml_text))
    return yaml_text


def _detect_connection_type(ciq_data: dict) -> dict:
    """Detect node connection type from CIQ columns (NIAM vs direct SSH)."""
    per_sheet = ciq_data.get("per_sheet", {})
    info = {"type": "ssh_direct", "details": {}}

    for sheet_name, sdata in per_sheet.items():
        headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
        headers_lower = [h.lower() for h in headers]

        # Check for NIAM columns
        if any("niam" in h for h in headers_lower):
            info["type"] = "niam_proxy"
            info["details"]["niam_columns"] = [h for h in headers if "niam" in h.lower()]
            info["details"]["sheet"] = sheet_name

        # Check for direct SSH columns
        for pattern in ("ip", "host", "hostname", "ip_address", "ip_addr"):
            if any(pattern == h for h in headers_lower):
                info["details"]["ip_column"] = next(h for h in headers if h.lower() == pattern)

        for pattern in ("user", "username", "login"):
            if any(pattern == h for h in headers_lower):
                info["details"]["user_column"] = next(h for h in headers if h.lower() == pattern)

        for pattern in ("password", "passwd", "pwd"):
            if any(pattern == h for h in headers_lower):
                info["details"]["password_column"] = next(h for h in headers if h.lower() == pattern)

    return info


def _build_json_output_summary(run_dir: str, uc_name: str) -> str:
    """Read the JSON output from Step 5 ciq_output and summarize the structure."""
    json_path = Path(run_dir) / "ciq_output" / f"{uc_name}.json"
    if not json_path.exists():
        return ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        cr_groups = data.get("crGroups", [])
        if not cr_groups:
            return json.dumps(data, indent=2, default=str)[:3000]

        # Summarize structure
        cg = cr_groups[0]
        ng = cg.get("nodeGroups", [{}])[0] if cg.get("nodeGroups") else {}
        node_sample = ng.get("nodes", [{}])[0] if ng.get("nodes") else {}
        config_sample = ng.get("configData", [{}])[0] if ng.get("configData") else {}

        summary = {
            "schemaVersion": data.get("schemaVersion"),
            "crGroup_count": len(cr_groups),
            "crGroup_fields": list(cg.keys()),
            "nodeGroup_fields": list(ng.keys()),
            "node_fields": list(node_sample.keys()),
            "configData_fields": list(config_sample.keys()),
            "sample_node": node_sample,
            "sample_configData": config_sample,
        }
        return json.dumps(summary, indent=2, default=str)
    except Exception:
        return ""


class WorkflowGenAgent:
    step_number = 6
    step_name = "Workflow Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(6, config)

        schema_path = Path("schemas/cli-automation-workflow-schema_v1.2.yaml")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

        few_shot_text = ""
        if few_shot_examples:
            for ex in few_shot_examples[:2]:
                if "workflow" in ex:
                    few_shot_text += f"\n--- Example from UC '{ex['uc_name']}' ---\n{ex['workflow']}\n"

        # Build rich context from all available data
        uc_name = input_data.get("uc_name", "UC")
        parts = uc_name.split("_", 1)
        node_type = parts[0] if len(parts) == 2 else uc_name
        activity = parts[1] if len(parts) == 2 else ""

        # ── Requirements from RequirementsGatherer (authoritative) ──
        requirements = input_data.get("requirements", {})
        gen_hints = input_data.get("generation_hints", {})
        wf_hints = gen_hints.get("workflow_yaml", {}) if isinstance(gen_hints.get("workflow_yaml"), dict) else {}
        raw_artifacts = input_data.get("raw_artifacts", {})
        dev_answers = input_data.get("developer_answers", {})

        # Determine which phases to generate (from requirements, not hardcoded)
        orch = requirements.get("category_3_orchestration", {})
        req_phases = orch.get("phases", ["preNodeHealthCheck", "activity", "postNodeHealthCheck"])
        phase_overrides = wf_hints.get("phase_overrides", [])

        # Build requirements context for the prompt
        req_context_parts = []
        if requirements:
            # Node configs from requirements
            node_info = requirements.get("category_1_node_activity", {})
            if node_info.get("nodes"):
                req_context_parts.append("=== NODE CONFIGURATION (from requirements) ===")
                for n in node_info["nodes"]:
                    req_context_parts.append(
                        f"  Node: {n.get('name')} ({n.get('role')})\n"
                        f"    SSH user: {n.get('ssh', {}).get('user', 'unknown')}\n"
                        f"    Prompt regex: {n.get('prompt_regex', 'unknown')}\n"
                        f"    Escalation: {n.get('escalation', {}).get('method', 'none')}\n"
                        f"    Escalation prompt: {n.get('escalation', {}).get('prompt_regex', 'none')}"
                    )

            # Commands from requirements
            cmd_info = requirements.get("category_2_commands", {})
            if cmd_info.get("commands"):
                req_context_parts.append("\n=== COMMANDS (from requirements) ===")
                for c in cmd_info["commands"]:
                    req_context_parts.append(
                        f"  {c.get('cmd')} → node: {c.get('node')}, purpose: {c.get('purpose')}"
                    )

            # Phase instructions
            req_context_parts.append(f"\n=== REQUIRED PHASES ===\n  {req_phases}")
            if phase_overrides:
                req_context_parts.append("=== PHASE CONSTRAINTS (MUST OBEY) ===")
                for po in phase_overrides:
                    req_context_parts.append(f"  *** {po} ***")

            # Conditional logic hints
            cond_logic = wf_hints.get("conditional_logic", [])
            if cond_logic:
                req_context_parts.append("\n=== CONDITIONAL LOGIC INSTRUCTIONS ===")
                for cl in cond_logic:
                    req_context_parts.append(f"  {cl}")

        requirements_text = "\n".join(req_context_parts) if req_context_parts else ""

        # Developer answers — direct quotes for context
        dev_answers_text = ""
        if dev_answers:
            relevant = {k: v for k, v in dev_answers.items()
                        if any(kw in k.lower() for kw in ("backup", "rollback", "phase", "prompt",
                               "login", "ssh", "escalat", "command", "xml", "sftp"))}
            if relevant:
                dev_answers_text = (
                    "\n=== DEVELOPER ANSWERS (AUTHORITATIVE — follow these exactly) ===\n"
                    + json.dumps(relevant, indent=2, default=str)[:3000]
                )

        # MOP data — prefer raw MOP text over parsed templates
        raw_mop = wf_hints.get("raw_mop_text") or raw_artifacts.get("mop_text", "")
        mop_data = input_data.get("mop_data", {})
        if raw_mop:
            mop_summary = f"=== RAW MOP DOCUMENT ===\n{raw_mop[:10000]}"
        else:
            mop_summary = json.dumps(mop_data, indent=2, default=str)[:6000] if mop_data else "No MOP data available"

        # Command outputs — prefer raw SSH logs over parsed pairs
        raw_ssh = raw_artifacts.get("ssh_logs", "")
        cmd_outputs = input_data.get("command_outputs", {})
        if raw_ssh:
            cmd_summary = f"=== RAW SSH SESSION LOGS ===\n{raw_ssh[:15000]}"
        else:
            cmd_summary = json.dumps(cmd_outputs, indent=2, default=str)[:4000] if cmd_outputs else "No command outputs available"

        # XML samples
        xml_samples = wf_hints.get("xml_samples") or raw_artifacts.get("xml_samples", "")
        xml_samples_text = ""
        if xml_samples:
            xml_samples_text = f"\n=== SAMPLE XML FILES ===\n{xml_samples[:5000]}\n"

        # CIQ data — columns and sample rows
        ciq_data = input_data.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})
        ciq_parts = []
        for sname, sdata in per_sheet.items():
            headers = sdata.get("headers", []) if isinstance(sdata, dict) else getattr(sdata, "headers", [])
            samples = sdata.get("sample_rows", []) if isinstance(sdata, dict) else getattr(sdata, "sample_rows", [])
            ciq_parts.append(f'Sheet "{sname}": columns={headers}')
            if samples:
                for r in samples[:2]:
                    vals = {k: v for k, v in (r.items() if isinstance(r, dict) else r) if v}
                    ciq_parts.append(f"  sample: {vals}")
        ciq_summary = "\n".join(ciq_parts) if ciq_parts else "No CIQ data available"

        # Detect connection type
        conn_info = _detect_connection_type(ciq_data)
        conn_desc = f"Connection type: {conn_info['type']}"
        if conn_info["details"]:
            conn_desc += f"\n  Details: {json.dumps(conn_info['details'])}"

        # JSON output structure from Step 5
        run_dir = input_data.get("run_dir", ".")
        json_output_summary = _build_json_output_summary(run_dir, uc_name)

        # Retry info
        retry_info = ""
        if retry_context:
            retry_info = (
                f"\n\nPREVIOUS ATTEMPT FAILED:\n"
                f"{json.dumps(retry_context.previous_error, indent=2)}\n"
                "Fix the issues and regenerate."
            )

        # Execution feedback from Step 9 failures
        exec_feedback = input_data.get("exec_feedback", [])
        exec_feedback_text = ""
        if exec_feedback:
            exec_feedback_text = (
                "\n\n=== EXECUTION TEST FAILURES (from previous run) ===\n"
                "The following commands failed during execution testing.\n"
                "Fix the register regex patterns and validation expressions so they work correctly.\n"
                "Common issues:\n"
                "- Validation expr uses 'and'/'or' instead of '&&'/'||'\n"
                "- Register regex doesn't match the actual command output format\n"
                "- Validation checks values that the register can't capture\n"
                "- Expression uses unsupported functions like Integer.parseInt()\n\n"
                f"{json.dumps(exec_feedback, indent=2, default=str)[:3000]}\n"
            )

        prompt = (
            f"Generate a COMPLETE cli-automation workflow YAML for UC: {uc_name}\n"
            f"Node Type: {node_type}, Activity: {activity}\n\n"

            f"{requirements_text}\n\n"

            f"{dev_answers_text}\n\n"

            f"=== CIQ DATA (node connection info) ===\n{ciq_summary}\n\n"
            f"{conn_desc}\n\n"

            f"=== JSON OUTPUT STRUCTURE (runtime data available via ${{}} variables) ===\n"
            f"{json_output_summary or 'Not available'}\n\n"

            f"=== MOP DATA (procedure steps to automate) ===\n{mop_summary}\n\n"

            f"=== COMMAND OUTPUT SAMPLES (expected CLI responses) ===\n{cmd_summary}\n\n"

            f"{xml_samples_text}"

            f"{few_shot_text}{retry_info}{exec_feedback_text}\n\n"

            "=== WORKING EXAMPLE (follow this EXACT structure) ===\n\n"
            "version: '1.2'\n"
            "name: example-workflow\n"
            "globals:\n"
            "  vars:\n"
            "    ACTIVITY_ID: '1029_TAC_LOADING'\n"
            "    NODE_TYPE: EIR\n"
            "    ROLLBACK_REQUIRED: 'false'\n"
            "  secrets:\n"
            "    node_password:\n"
            "      value: '${ENV.NODE_PASSWORD}'\n"
            "      redacted: true\n"
            "  defaults:\n"
            "    timeout_sec: 120\n"
            "    on_failure: stop\n"
            "nodes:\n"
            "  target_node:\n"
            "    type: remote\n"
            "    plugin: ssh\n"
            "    prompt_regex: '(?m)[#$]\\s*$'\n"
            "    ssh:\n"
            "      host: '${node_ip}'\n"
            "      user: '${node_user}'\n"
            "      auth:\n"
            "        password: '${SECRET.node_password}'\n"
            "      strict_host_key_checking: false\n"
            "phases:\n"
            "  preNodeHealthCheck:\n"
            "    name: Pre-Node Health Check\n"
            "    on_failure: stop\n"
            "    steps:\n"
            "      - node: target_node\n"
            "        command_description: Verify connectivity\n"
            "        send: hostname\n"
            "        on_failure: stop\n"
            "        register:\n"
            "          - regex: '(?<hostName>\\S+)'\n"
            "        validation:\n"
            "          enabled: true\n"
            "          description: Verify SSH connectivity\n"
            "          success:\n"
            "            criteria:\n"
            "              expr: '${hostName != \"\"}'\n"
            "            message: Connected to ${hostName}\n"
            "          failure:\n"
            "            message: Failed to connect\n"
            "      - node: target_node\n"
            "        command_description: Check alarms\n"
            "        send: 'alarm_cli --list critical'\n"
            "        on_failure: continue\n"
            "        register:\n"
            "          - regex: '(?<alarmCount>\\d+) alarm'\n"
            "  backup:\n"
            "    name: Configuration Backup\n"
            "    steps:\n"
            "      - node: target_node\n"
            "        command_description: Take config backup\n"
            "        send: 'cp /config/running.xml /backup/'\n"
            "        on_failure: stop\n"
            "  activity_configuration:\n"
            "    name: Activity Configuration\n"
            "    steps:\n"
            "      - node: target_node\n"
            "        command_description: Apply configuration\n"
            "        send: 'config apply ${ACTIVITY_ID}'\n"
            "        on_failure: stop\n"
            "        register:\n"
            "          - regex: '(?<applyResult>SUCCESS|FAILED)'\n"
            "        validation:\n"
            "          enabled: true\n"
            "          success:\n"
            "            criteria:\n"
            "              expr: '${applyResult == \"SUCCESS\"}'\n"
            "            message: Configuration applied successfully\n"
            "          failure:\n"
            "            message: Configuration apply failed\n\n"

            "=== WORKFLOW GENERATION INSTRUCTIONS ===\n\n"

            f"Generate a PRODUCTION-READY workflow with EXACTLY these phases: {req_phases}\n"
            "IMPORTANT: Only generate the phases listed above. Do NOT add extra phases.\n\n"

            + (
                "".join(f"*** CONSTRAINT: {po} ***\n" for po in phase_overrides) + "\n"
                if phase_overrides else ""
            ) +

            "Phase descriptions (only generate phases that are in the list above):\n\n"

            "- preNodeHealthCheck phase:\n"
            "   - Verify node connectivity (SSH login test)\n"
            "   - Check node health status (alarms, service state)\n"
            "   - Register discovered values for later use\n\n"

            "- backup phase (ONLY if listed in required phases above):\n"
            "   - Take configuration backup before changes\n"
            "   - Store backup on node and transfer to repository server\n"
            "   - Register backup file paths for rollback\n\n"

            "- activity phase (the main UC operation):\n"
            "   - Map EVERY MOP step to a workflow step\n"
            "   - Use for_each loops when operating on multiple items from configData\n"
            "   - Include validation after each significant command\n"
            "   - Register output values for post-check verification\n"
            "   - Handle errors with appropriate on_failure (stop/continue)\n\n"

            "- postNodeHealthCheck phase:\n"
            "   - Re-run the same health checks as pre-check\n"
            "   - Compare pre vs post values where applicable\n"
            "   - Verify the activity changes were applied correctly\n\n"

            "- rollback phase (ONLY if listed in required phases above):\n"
            "   - Include when condition: '${ROLLBACK_REQUIRED == \"true\"}'\n"
            "   - Reverse the activity changes using backup data\n\n"

            "=== NODE CONNECTION RULES ===\n\n"

            "Detect connection type from CIQ columns:\n"
            "- If CIQ has NIAM_NAME/NIAM_ID columns → use NIAM proxy SSH:\n"
            "  node type: remote, plugin: ssh, ne_id: ${niamID}\n"
            "  ssh: { host: ${NIAM_IP}, port: ${M2MPORT}, user: ${M2MUSER}, auth: { password: ${M2MPASSWORD} } }\n"
            "- If CIQ has IP/Host + Username/Password columns → use direct SSH:\n"
            "  node type: remote, plugin: ssh\n"
            "  ssh: { host: ${node_ip}, user: ${node_user}, auth: { password: ${SECRET.node_password} } }\n"
            "- If CIQ has only node names → assume NIAM proxy\n"
            "- Add a repo_server node ONLY if backup phase is required or file transfer is needed\n\n"

            "=== EXACT STEP STRUCTURE (Java model) ===\n\n"

            "CORRECT step format (follow EXACTLY):\n"
            "  - node: target_node\n"
            "    command_description: Check connectivity\n"
            "    send: hostname\n"
            "    on_failure: stop\n"
            "    register:\n"
            "      - regex: '(?<hostName>\\S+)'\n"
            "    validation:\n"
            "      enabled: true\n"
            "      description: Verify connectivity\n"
            "      success:\n"
            "        criteria:\n"
            "          expr: '${hostName != \"\"}'\n"
            "        message: Connected successfully\n"
            "      failure:\n"
            "        message: Failed to connect\n\n"

            "REGISTER: must be a LIST of items with regex or name/value:\n"
            "  register:\n"
            "    - regex: '(?<varName>pattern)'\n"
            "    - name: derivedVar\n"
            "      value: '${someVar}'\n"
            "WRONG register formats: register: {name: regex} or register: name: regex\n\n"

            "FOR_EACH loops (iterate over configData items):\n"
            "  - for_each: '${configDataList}'\n"
            "    item_var: currentItem\n"
            "    steps:\n"
            "      - node: target_node\n"
            "        command_description: Process item\n"
            "        send: 'process ${currentItem}'\n"
            "WRONG loop format: '- loop: { id: ..., items: ... }' — 'loop' is NOT a valid step field\n\n"

            "VALIDATION: must be a single OBJECT (not a list):\n"
            "  validation:\n"
            "    enabled: true\n"
            "    success:\n"
            "      criteria:\n"
            "        expr: '${VAR != \"\"}'\n"
            "      message: OK\n"
            "    failure:\n"
            "      message: Failed\n"
            "WRONG validation formats: validation: [list], success_expr, success_regex\n\n"

            "FORBIDDEN step fields: id, step_id, step_name, loop — use command_description instead of id, use for_each instead of loop\n"
            "FORBIDDEN register fields: error_on_missing, default_value, optional — these are NOT recognized by the Java model\n"
            "ONLY valid register fields: regex, name, value, when, loop, start_index, count_var\n\n"

            "=== VARIABLE NAMING ===\n\n"
            "Use ${varName} syntax. At runtime, the framework provides:\n"
            "- CIQ JSON fields: ${node}, ${niamID}, ${crGroup}, ${nodeGroup}, ${email}\n"
            "- configData fields: each configData field name directly, e.g. ${Test IMSI}\n"
            "- Registered values: ${VAR_NAME} from previous step register blocks\n"
            "- Globals: ${activity_id}, ${node_type}, etc.\n"
            "- Secrets: ${SECRET.key_name}\n"
            "- Environment: ${ENV.VAR_NAME}\n\n"

            "=== CRITICAL ===\n"
            "- Generate AT LEAST 15-20 steps total across all phases\n"
            "- Map EVERY MOP procedure step — do not skip or summarize\n"
            "- Use register with (?<namedGroup>...) regex for every step that captures output\n"
            "- CRITICAL: Java regex named groups MUST use camelCase with NO underscores: (?<hostName>...), (?<alarmCount>...), (?<systemStatus>...)\n"
            "  WRONG: (?<CRITICAL_ALARMS>...) (?<SYSTEM_STATUS>...) — underscores are NOT allowed in Java 8 regex group names\n"
            "- Use validation: { enabled: true, success: { criteria: { expr: ... } } } format\n"
            "- phases MUST be a MAP (key: value), NOT a list\n"
            "- Steps MUST NOT have 'id' field — use command_description instead\n"
            "- Use && and || for boolean operators in expr (NOT 'and'/'or' — Java expression engine)\n"
            "- Validation expr MUST use SIMPLE comparisons only:\n"
            "  CORRECT: '${varName == \"expected\"}', '${varName != \"\"}', '${count == \"0\"}'\n"
            "  WRONG: '!${var}.contains(\"x\")', 'Integer.parseInt(var)', '${var.toInteger()}'\n"
            "  The expression engine only supports ==, !=, &&, || with string values\n"
            "- Use 2-space indentation consistently, NO tabs\n"
            "- Quote strings with special chars: colons, braces, brackets, #\n"
            "- Multi-line send commands use YAML literal block: send: |\n"
            "- Return ONLY valid YAML, no markdown fences"
        )

        system = (
            "You are a telecom network automation expert for the cr-automation CLI framework. "
            "You generate production-ready workflow YAML v1.2 files that execute MOP procedures "
            "against network nodes via SSH. You ONLY generate the phases specified in the requirements — "
            "do NOT add backup or rollback phases unless explicitly requested. "
            "You follow developer answers and requirements exactly as given. "
            "You handle both NIAM proxy and direct SSH connections based on CIQ data."
        )

        response = await call_llm(prompt, system_prompt=system, model=model)
        yaml_content = _extract_yaml(response)

        # Save raw output for debugging
        try:
            raw_path = Path(input_data.get("run_dir", ".")) / f"{uc_name}_workflow_raw.yaml"
            raw_path.write_text(yaml_content, encoding="utf-8")
        except Exception:
            pass

        # Auto-fix common YAML issues
        yaml_content = _fix_workflow_yaml(yaml_content)

        # Validate YAML syntax
        try:
            _validate_yaml(yaml_content)
        except Exception as e:
            # Extract the problematic line for the retry context
            err_str = str(e)
            lines = yaml_content.split("\n")
            # Try to find line number from error
            line_match = re.search(r'line (\d+)', err_str)
            context_lines = ""
            if line_match:
                ln = int(line_match.group(1))
                start = max(0, ln - 3)
                end = min(len(lines), ln + 3)
                context_lines = "\n".join(
                    f"{'>>>' if i+1 == ln else '   '} {i+1}: {lines[i]}"
                    for i in range(start, end)
                )
            logger.warning("Generated workflow YAML invalid: %s", e)
            return StepOutput(
                status="failed",
                error={
                    "message": f"Workflow YAML syntax error: {err_str}",
                    "context": context_lines,
                    "fix_hint": "Check indentation — all steps must be at the same indent level within a phase. "
                                "Use 2-space indentation consistently. Do not mix tabs and spaces.",
                },
                output_data={"workflow_yaml": yaml_content},
            )

        # Validate workflow has required structure
        errors = _validate_workflow_structure(yaml_content)
        if errors:
            return StepOutput(
                status="failed",
                error={"message": "Workflow structure issues:\n" + "\n".join(errors)},
                output_data={"workflow_yaml": yaml_content},
            )

        return StepOutput(
            status="passed",
            output_data={"workflow_yaml": yaml_content},
            artifacts=[],
        )


def _fix_workflow_yaml(yaml_text: str) -> str:
    """Auto-fix common YAML issues in LLM-generated workflows."""
    # Remove BOM
    yaml_text = yaml_text.strip().lstrip("\ufeff")

    # Strip control characters (LLM sometimes generates ASCII 0x00-0x08, 0x0B-0x0C, 0x0E-0x1F)
    yaml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', yaml_text)

    # Fix tabs → 2 spaces
    yaml_text = yaml_text.replace("\t", "  ")

    # Fix trailing whitespace
    yaml_text = "\n".join(line.rstrip() for line in yaml_text.split("\n"))

    # Quote unquoted values that contain colons (common LLM issue)
    # Matches lines like "    message: some text with Queries: ${var}"
    # where the value part has an unquoted colon that breaks YAML parsing
    def _quote_colon_values(line: str) -> str:
        # Skip lines that are already quoted or are comments
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            return line
        # Match key: value where value is unquoted and contains a colon
        m = re.match(r'^(\s*\w[\w\s]*?:\s+)(.+)$', line)
        if m:
            indent_key = m.group(1)
            value = m.group(2)
            # Skip if already quoted
            if value.startswith(("'", '"', "|", ">", "[", "{")):
                return line
            # Check if value contains an unquoted colon (not ${...} refs)
            val_no_refs = re.sub(r'\$\{[^}]*\}', '', value)
            if ':' in val_no_refs:
                # Quote with single quotes, escaping existing single quotes
                value = "'" + value.replace("'", "''") + "'"
                return indent_key + value
        return line

    yaml_text = "\n".join(_quote_colon_values(l) for l in yaml_text.split("\n"))

    # Fix single-quoted values with nested single quotes inside
    # e.g., send: 'echo "${data['col']}"' → send: "echo \"${data['col']}\""
    # Note: backslashes are FINE in single-quoted YAML (literal, not escapes)
    def _fix_nested_quotes(line: str) -> str:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            return line
        # Match key: 'value...' pattern
        m = re.match(r"^(\s*[\w-]+:\s+)'(.+)'\s*$", line)
        if not m:
            # Also try list item: - key: 'value...'
            m = re.match(r"^(\s*-\s+[\w-]+:\s+)'(.+)'\s*$", line)
        if not m:
            return line
        indent_key = m.group(1)
        content = m.group(2)
        # Only fix if there are actual nested single quotes (not doubled '')
        # Backslashes in single-quoted YAML are literal — do NOT switch to double-quotes for them
        if "'" in content and "''" not in content:
            # Switch to double-quoted — escape internal double quotes and backslashes
            content = content.replace('\\', '\\\\').replace('"', '\\"')
            return f'{indent_key}"{content}"'
        return line

    yaml_text = "\n".join(_fix_nested_quotes(l) for l in yaml_text.split("\n"))

    # Fix invalid escape sequences in double-quoted strings
    # YAML double quotes only allow specific escapes (\n \t \\ \" etc.)
    # LLM often uses regex escapes like \$ \( \) \| which are invalid in YAML double quotes
    # Fix: convert such strings to single-quoted
    def _fix_double_quoted_regex(m):
        content = m.group(1)
        if any(f"\\{c}" in content for c in "$(){}[].|+*?^"):
            content = content.replace("'", "''")
            return f"'{content}'"
        return m.group(0)

    yaml_text = re.sub(r'"((?:[^"\\]|\\.)*)"', _fix_double_quoted_regex, yaml_text)

    # Strip invalid fields from node definitions
    yaml_text = _fix_node_definitions(yaml_text)

    # Fix for_each at phase level — move into a wrapper step
    yaml_text = _fix_for_each_at_phase_level(yaml_text)

    # Fix for_each steps missing 'node' — Java JAR requires it on every step
    yaml_text = _fix_missing_node_in_steps(yaml_text)

    # Rename invalid step fields to valid Java model equivalents
    yaml_text = _rename_invalid_step_fields(yaml_text)

    # Strip invalid register item properties that the Java model doesn't recognize
    yaml_text = _strip_invalid_register_fields(yaml_text)

    # Strip invalid fields from validation branches (success/failure/warning)
    yaml_text = _strip_invalid_validation_fields(yaml_text)

    # Fix register items: ensure regex has named capture group matching the 'name' field
    yaml_text = _fix_register_capture_groups(yaml_text)

    # Fix Java 8 regex named group names — underscores not allowed
    # Convert (?<SOME_NAME>...) → (?<someName>...)
    yaml_text = _fix_regex_group_names(yaml_text)

    # Fix boolean operators in validation expressions: 'and' → '&&', 'or' → '||'
    yaml_text = _fix_boolean_operators(yaml_text)

    return yaml_text


def _fix_boolean_operators(yaml_text: str) -> str:
    """Fix Python-style boolean operators in validation expr fields.

    The Java expression engine uses && and || but LLM often generates
    'and' and 'or'. Replace only within expr: '...' contexts.
    """
    # Match expr values in single or double quotes
    def _fix_expr(m):
        expr = m.group(1)
        # Replace word-boundary 'and'/'or' with &&/||
        expr = re.sub(r'\band\b', '&&', expr)
        expr = re.sub(r'\bor\b', '||', expr)
        return f"expr: '{expr}'"

    yaml_text = re.sub(r"expr:\s*'([^']*)'", _fix_expr, yaml_text)
    yaml_text = re.sub(r'expr:\s*"([^"]*)"', lambda m: f'expr: \'{m.group(1)}\'', yaml_text)
    return yaml_text


def _fix_node_definitions(yaml_text: str) -> str:
    """Strip invalid fields from node definitions and move misplaced fields into ssh.

    Java NodeDefinition valid fields: type, plugin, ssh, prompt_regex
    SSH fields: host, port, user, auth, ne_id, strict_host_key_checking
    LLM often puts ne_id, host, user directly on the node instead of inside ssh.
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        nodes = data.get("nodes", {})
        if not isinstance(nodes, dict):
            return yaml_text

        _VALID_NODE_FIELDS = {"type", "plugin", "ssh", "prompt_regex"}
        _SSH_FIELDS = {"host", "port", "user", "auth", "ne_id", "strict_host_key_checking", "password"}

        changed = False
        for node_name, node_def in nodes.items():
            if not isinstance(node_def, dict):
                continue
            ssh = node_def.get("ssh", {})
            if not isinstance(ssh, dict):
                ssh = {}

            # Move SSH-related fields from node level into ssh section
            for field in list(node_def.keys()):
                if field in _SSH_FIELDS:
                    if field not in ssh:
                        ssh[field] = node_def[field]
                    del node_def[field]
                    changed = True
                    logger.info("Auto-fix: moved node.%s.%s into ssh section", node_name, field)

            if ssh and "ssh" not in node_def:
                node_def["ssh"] = ssh
                changed = True

            # Remove any remaining invalid fields
            for field in list(node_def.keys()):
                if field not in _VALID_NODE_FIELDS:
                    del node_def[field]
                    changed = True
                    logger.info("Auto-fix: removed invalid node field '%s' from '%s'", field, node_name)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to fix node definitions: %s", e)
    return yaml_text


def _fix_for_each_at_phase_level(yaml_text: str) -> str:
    """Move for_each/item_var from phase level into a wrapper step.

    PhaseBlockDefinition only supports name, on_failure, when, steps.
    If for_each is on the phase, wrap all steps in a for_each step.
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        changed = False
        for pname, pdef in phases.items():
            if not isinstance(pdef, dict):
                continue
            if "for_each" not in pdef:
                continue

            # Extract for_each fields from phase
            for_each = pdef.pop("for_each")
            item_var = pdef.pop("item_var", "currentItem")
            max_iter = pdef.pop("max_iterations", None)
            original_steps = pdef.get("steps", [])

            # Wrap in a for_each step
            wrapper = {
                "for_each": for_each,
                "item_var": item_var,
                "steps": original_steps,
            }
            if max_iter is not None:
                wrapper["max_iterations"] = max_iter

            pdef["steps"] = [wrapper]
            changed = True
            logger.info("Moved for_each from phase '%s' into wrapper step", pname)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to fix for_each at phase level: %s", e)
    return yaml_text


def _rename_invalid_step_fields(yaml_text: str) -> str:
    """Rename invalid step field names to their Java model equivalents.

    Common LLM mistakes:
    - 'if' → 'when' (conditional execution)
    - 'loop' → 'for_each' (iteration)
    - 'id' / 'step_id' / 'step_name' → removed (use command_description)
    - 'send_options' → removed (not in Java model)
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        _RENAME_MAP = {
            "if": "when", "loop": "for_each", "cmd": "send", "command": "send",
            "name": "command_description", "description": "command_description",
        }
        # Whitelist of valid StepDefinition fields in Java model
        _VALID_STEP_FIELDS = {
            "node", "command_description", "send", "on_failure", "timeout_sec",
            "register", "validation", "retries", "retry_delay_sec", "when",
            "skip_when", "expect_reply", "prompt_regex", "for_each", "enabled",
            "interaction_timeout_sec", "steps",
            "item_var", "max_iterations", "continue_when", "break_when",
            "rest", "sftp", "email",
        }

        changed = False

        def _fix_steps(steps: list):
            nonlocal changed
            for step in steps:
                if not isinstance(step, dict):
                    continue
                # Rename fields
                for old, new in _RENAME_MAP.items():
                    if old in step and new not in step:
                        step[new] = step.pop(old)
                        changed = True
                        logger.info("Renamed step field '%s' -> '%s'", old, new)
                # Remove any field not in the whitelist
                for key in list(step.keys()):
                    if key not in _VALID_STEP_FIELDS:
                        del step[key]
                        changed = True
                        logger.info("Removed invalid step field '%s'", key)
                # Recurse
                nested = step.get("steps", [])
                if isinstance(nested, list):
                    _fix_steps(nested)

        for pdef in phases.values():
            if isinstance(pdef, dict):
                steps = pdef.get("steps", [])
                if isinstance(steps, list):
                    _fix_steps(steps)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to rename invalid step fields: %s", e)
    return yaml_text


def _find_node_in_steps(steps: list) -> str | None:
    """Recursively find the first 'node' value in a list of steps."""
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("node"):
            return step["node"]
        nested = step.get("steps", [])
        if isinstance(nested, list):
            found = _find_node_in_steps(nested)
            if found:
                return found
    return None


def _fix_missing_node_in_steps(yaml_text: str) -> str:
    """Inject 'node' into for_each/wrapper steps that are missing it.

    The Java JAR requires every step to have a 'node' field, including
    for_each wrapper steps. If missing, inherit from nested steps or
    fall back to 'target_node'.
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        # Determine default node from the 'nodes' section
        nodes_section = data.get("nodes", {})
        default_node = next(iter(nodes_section), "target_node") if isinstance(nodes_section, dict) else "target_node"

        changed = False

        def _fix_steps(steps: list):
            nonlocal changed
            for step in steps:
                if not isinstance(step, dict):
                    continue
                # Recurse into nested steps first
                nested = step.get("steps", [])
                if isinstance(nested, list):
                    _fix_steps(nested)
                # If step has no 'node', inject one
                if not step.get("node"):
                    inherited = _find_node_in_steps(nested) if isinstance(nested, list) else None
                    step["node"] = inherited or default_node
                    changed = True
                    logger.info("Injected node='%s' into step missing node field", step["node"])

        for pdef in phases.values():
            if isinstance(pdef, dict):
                steps = pdef.get("steps", [])
                if isinstance(steps, list):
                    _fix_steps(steps)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to fix missing node in steps: %s", e)
    return yaml_text


def _fix_register_capture_groups(yaml_text: str) -> str:
    """Fix register items to ensure regex has a named capture group matching the 'name' field.

    The Java framework requires register items to have a named capture group (?<varName>...)
    in the regex to capture values into variables. LLM often generates:
      - regex: 'some text'  name: varName  → should be regex: '(?<varName>some text)'
      - regex: 'pattern' without name → just a match pattern, OK
      - regex already has (?<...>) → leave as-is
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        changed = False

        def _fix_steps(steps: list):
            nonlocal changed
            for step in steps:
                if not isinstance(step, dict):
                    continue
                reg = step.get("register")
                if not isinstance(reg, list):
                    continue
                for item in reg:
                    if not isinstance(item, dict):
                        continue
                    regex = item.get("regex", "")
                    name = item.get("name", "")
                    if not regex or not name:
                        continue
                    # Skip if regex already has any named capture group
                    if "(?<" in regex:
                        continue
                    # Wrap the entire regex in a named capture group
                    item["regex"] = f"(?<{name}>{regex})"
                    changed = True
                    logger.info("Auto-fix: added capture group (?<%s>...) to register regex", name)
                # Recurse
                nested = step.get("steps", [])
                if isinstance(nested, list):
                    _fix_steps(nested)

        for pdef in phases.values():
            if isinstance(pdef, dict):
                steps = pdef.get("steps", [])
                if isinstance(steps, list):
                    _fix_steps(steps)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to fix register capture groups: %s", e)
    return yaml_text


def _fix_regex_group_names(yaml_text: str) -> str:
    """Convert underscored regex group names to camelCase for Java 8 compatibility.

    Java 8 regex named groups only allow [a-zA-Z][a-zA-Z0-9]* — no underscores.
    (?<SOME_NAME>...) → (?<someName>...)
    Also fixes all ${SOME_NAME} variable references to match.
    """
    # Find all named groups with underscores
    group_pattern = re.compile(r'\(\?<([a-zA-Z]\w*?)>')
    names_to_fix = set()
    for m in group_pattern.finditer(yaml_text):
        name = m.group(1)
        if '_' in name:
            names_to_fix.add(name)

    if not names_to_fix:
        return yaml_text

    def _to_camel(name: str) -> str:
        parts = name.lower().split('_')
        return parts[0] + ''.join(p.capitalize() for p in parts[1:])

    for old_name in names_to_fix:
        new_name = _to_camel(old_name)
        # Replace in regex group definitions: (?<OLD_NAME> → (?<newName>
        yaml_text = yaml_text.replace(f'(?<{old_name}>', f'(?<{new_name}>')
        # Replace in variable references: ${OLD_NAME} and ${OLD_NAME
        yaml_text = yaml_text.replace(f'${{{old_name}}}', f'${{{new_name}}}')
        yaml_text = yaml_text.replace(f'${{{old_name} ', f'${{{new_name} ')
        yaml_text = yaml_text.replace(f'${{{old_name}.', f'${{{new_name}.')
        logger.info("Fixed regex group name: %s → %s", old_name, new_name)

    return yaml_text


# Valid fields for RegisterDefinition (Java model)
_VALID_REGISTER_FIELDS = {"regex", "name", "value", "when", "loop", "start_index", "count_var"}


def _strip_invalid_register_fields(yaml_text: str) -> str:
    """Parse YAML and remove any unrecognized fields from register items."""
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        changed = False
        for pdef in phases.values():
            if not isinstance(pdef, dict):
                continue
            steps = pdef.get("steps", [])
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                reg = step.get("register")
                if not isinstance(reg, list):
                    continue
                for item in reg:
                    if not isinstance(item, dict):
                        continue
                    bad_keys = [k for k in item if k not in _VALID_REGISTER_FIELDS]
                    for k in bad_keys:
                        del item[k]
                        changed = True
                        logger.info("Stripped invalid register field: %s", k)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to strip invalid register fields: %s", e)
    return yaml_text


_VALID_VALIDATION_FIELDS = {"enabled", "success", "failure", "warning"}
_VALID_VALIDATION_BRANCH_FIELDS = {"criteria", "message"}


def _strip_invalid_validation_fields(yaml_text: str) -> str:
    """Fix validation structure to match Java ValidationDefinition.

    Fixes:
    1. Remove invalid fields from validation (description, etc.)
    2. Remove invalid fields from success/failure/warning branches
    3. Fix criteria as string → {expr: '...'} object
    4. Fix criteria as multi-line block → single expr
    """
    from ruamel.yaml import YAML
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(yaml_text))
        if not isinstance(data, dict):
            return yaml_text

        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            return yaml_text

        changed = False

        def _clean_steps(steps: list):
            nonlocal changed
            for step in steps:
                if not isinstance(step, dict):
                    continue
                val = step.get("validation")
                if isinstance(val, dict):
                    # Remove invalid top-level validation fields
                    bad_top = [k for k in val if k not in _VALID_VALIDATION_FIELDS]
                    for k in bad_top:
                        del val[k]
                        changed = True
                        logger.info("Stripped invalid validation field: %s", k)

                    for branch_key in ("success", "failure", "warning"):
                        branch = val.get(branch_key)
                        if isinstance(branch, dict):
                            # Remove invalid branch fields
                            bad = [k for k in branch if k not in _VALID_VALIDATION_BRANCH_FIELDS]
                            for k in bad:
                                del branch[k]
                                changed = True
                                logger.info("Stripped invalid validation.%s field: %s", branch_key, k)

                            # Fix criteria: string → {expr: '...'}
                            crit = branch.get("criteria")
                            if isinstance(crit, str):
                                # Multi-line JS code → flatten to single expression
                                crit = crit.strip()
                                if "\n" in crit:
                                    # Complex multi-line → simplify to 'true' (can't parse JS)
                                    crit = "true"
                                    logger.info("Simplified multi-line validation criteria to 'true'")
                                branch["criteria"] = {"expr": crit}
                                changed = True
                                logger.info("Fixed validation criteria: string → {expr: ...}")
                elif isinstance(val, list):
                    # validation as list → invalid, convert first item or remove
                    step["validation"] = val[0] if val and isinstance(val[0], dict) else {"enabled": True}
                    changed = True

                nested = step.get("steps", [])
                if isinstance(nested, list):
                    _clean_steps(nested)

        for pdef in phases.values():
            if isinstance(pdef, dict):
                steps = pdef.get("steps", [])
                if isinstance(steps, list):
                    _clean_steps(steps)

        if changed:
            out = StringIO()
            yaml.dump(data, out)
            return out.getvalue()
    except Exception as e:
        logger.warning("Failed to strip invalid validation fields: %s", e)
    return yaml_text


def _validate_workflow_structure(yaml_text: str) -> list[str]:
    """Validate the workflow has required sections and sufficient steps."""
    from ruamel.yaml import YAML
    yaml = YAML()
    data = yaml.load(StringIO(yaml_text))
    if not isinstance(data, dict):
        return ["Root must be a YAML mapping"]

    errors = []

    if not data.get("globals"):
        errors.append("Missing 'globals' section (need vars, defaults)")
    if not data.get("nodes"):
        errors.append("Missing 'nodes' section (need at least one SSH node)")

    phases = data.get("phases", {})
    if not phases:
        errors.append("Missing 'phases' section")
    elif isinstance(phases, list):
        errors.append("'phases' must be a map (key: value), not a list. Use phase_name: { steps: [...] }")

    # Validate phase-level fields
    VALID_PHASE_FIELDS = {"name", "on_failure", "when", "steps"}
    if isinstance(phases, dict):
        for pname, pdef in phases.items():
            if isinstance(pdef, dict):
                bad_phase_keys = set(pdef.keys()) - VALID_PHASE_FIELDS
                if bad_phase_keys:
                    errors.append(
                        f"Phase '{pname}' has invalid fields {bad_phase_keys}. "
                        f"Only {VALID_PHASE_FIELDS} are allowed on phases. "
                        f"'for_each'/'item_var' belong inside a step, not on the phase."
                    )

    # Count total steps and validate step fields
    VALID_STEP_FIELDS = {
        "node", "command_description", "send", "on_failure", "timeout_sec",
        "register", "validation", "retries", "retry_delay_sec", "when",
        "skip_when", "expect_reply", "prompt_regex", "for_each", "enabled",
        "interaction_timeout_sec", "name", "description", "steps",
        "item_var", "max_iterations", "continue_when", "break_when",
        "rest", "sftp", "email",
    }
    INVALID_STEP_FIELDS = {"id", "step_id", "step_name", "loop"}

    total_steps = 0
    step_errors_found = 0
    if isinstance(phases, dict):
        for pname, pdef in phases.items():
            if not isinstance(pdef, dict):
                continue
            steps = pdef.get("steps", [])
            if not isinstance(steps, list):
                continue
            total_steps += len(steps)

            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue

                # Check for invalid fields
                for bad in INVALID_STEP_FIELDS:
                    if bad in step:
                        errors.append(
                            f"Phase '{pname}', step {i+1}: field '{bad}' is NOT valid. "
                            f"Remove it. Use 'command_description' instead."
                        )
                        step_errors_found += 1

                # Check register format
                reg = step.get("register")
                if reg is not None and not isinstance(reg, list):
                    errors.append(
                        f"Phase '{pname}', step {i+1}: 'register' must be a LIST of items "
                        f"[{{regex: '(?<NAME>...)'}}], not {type(reg).__name__}. "
                        f"Got: {str(reg)[:80]}"
                    )
                    step_errors_found += 1
                elif isinstance(reg, list):
                    for ri, ritem in enumerate(reg):
                        if isinstance(ritem, dict):
                            bad_rkeys = set(ritem.keys()) - _VALID_REGISTER_FIELDS
                            if bad_rkeys:
                                errors.append(
                                    f"Phase '{pname}', step {i+1}, register[{ri}]: "
                                    f"invalid fields {bad_rkeys}. "
                                    f"Only {_VALID_REGISTER_FIELDS} are allowed."
                                )
                                step_errors_found += 1

                # Check validation format
                val = step.get("validation")
                if val is not None and isinstance(val, list):
                    errors.append(
                        f"Phase '{pname}', step {i+1}: 'validation' must be a single OBJECT "
                        f"{{enabled: true, success: {{criteria: {{expr: ...}}}}}}, not a list."
                    )
                    step_errors_found += 1
                elif isinstance(val, dict):
                    # Check for wrong field names
                    bad_val_keys = {"success_expr", "success_regex", "failure_message",
                                    "fail_message", "success_message"}
                    found_bad = bad_val_keys & set(val.keys())
                    if found_bad:
                        errors.append(
                            f"Phase '{pname}', step {i+1}: validation has invalid keys {found_bad}. "
                            f"Use: validation: {{enabled: true, success: {{criteria: {{expr: '...'}}, message: '...'}}, failure: {{message: '...'}}}}"
                        )
                        step_errors_found += 1

                if step_errors_found >= 3:
                    errors.append("(truncated — fix the above issues first)")
                    return errors

    if total_steps < 5:
        errors.append(
            f"Only {total_steps} steps generated — expected at least 10-15. "
            "Include precheck, backup, activity, postcheck, rollback phases."
        )

    return errors
