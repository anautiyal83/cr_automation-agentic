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

        # MOP data
        mop_data = input_data.get("mop_data", {})
        mop_summary = json.dumps(mop_data, indent=2, default=str)[:6000] if mop_data else "No MOP data available"

        # Command outputs
        cmd_outputs = input_data.get("command_outputs", {})
        cmd_summary = json.dumps(cmd_outputs, indent=2, default=str)[:4000] if cmd_outputs else "No command outputs available"

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

        prompt = (
            f"Generate a COMPLETE cli-automation workflow YAML for UC: {uc_name}\n"
            f"Node Type: {node_type}, Activity: {activity}\n\n"

            f"=== CIQ DATA (node connection info) ===\n{ciq_summary}\n\n"
            f"{conn_desc}\n\n"

            f"=== JSON OUTPUT STRUCTURE (runtime data available via ${{}} variables) ===\n"
            f"{json_output_summary or 'Not available'}\n\n"

            f"=== MOP DATA (procedure steps to automate) ===\n{mop_summary}\n\n"

            f"=== COMMAND OUTPUT SAMPLES (expected CLI responses) ===\n{cmd_summary}\n\n"

            f"{few_shot_text}{retry_info}\n\n"

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
            "          - regex: '(?<HOSTNAME>\\S+)'\n"
            "        validation:\n"
            "          enabled: true\n"
            "          description: Verify SSH connectivity\n"
            "          success:\n"
            "            criteria:\n"
            "              expr: '${HOSTNAME != \"\"}'\n"
            "            message: Connected to ${HOSTNAME}\n"
            "          failure:\n"
            "            message: Failed to connect\n"
            "      - node: target_node\n"
            "        command_description: Check alarms\n"
            "        send: 'alarm_cli --list critical'\n"
            "        on_failure: continue\n"
            "        register:\n"
            "          - regex: '(?<ALARM_COUNT>\\d+) alarm'\n"
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
            "          - regex: '(?<APPLY_RESULT>SUCCESS|FAILED)'\n"
            "        validation:\n"
            "          enabled: true\n"
            "          success:\n"
            "            criteria:\n"
            "              expr: '${APPLY_RESULT == \"SUCCESS\"}'\n"
            "            message: Configuration applied successfully\n"
            "          failure:\n"
            "            message: Configuration apply failed\n\n"

            "=== WORKFLOW GENERATION INSTRUCTIONS ===\n\n"

            "Generate a PRODUCTION-READY workflow with these MANDATORY phases:\n\n"

            "1. PRE-NODE HEALTH CHECK phase:\n"
            "   - Verify node connectivity (SSH login test)\n"
            "   - Check node health status (alarms, service state)\n"
            "   - Identify IP version (IPv4/IPv6) if applicable\n"
            "   - Verify prerequisites (required files, permissions, namespaces)\n"
            "   - Register discovered values for later use\n\n"

            "2. BACKUP phase:\n"
            "   - Take configuration backup before changes\n"
            "   - Store backup on node and transfer to repository server\n"
            "   - Verify backup integrity (checksum comparison)\n"
            "   - Register backup file paths for rollback\n\n"

            "3. ACTIVITY CONFIGURATION phase (the main UC operation):\n"
            "   - Map EVERY MOP step to a workflow step\n"
            "   - Use for_each loops when operating on multiple items from configData\n"
            "   - Include validation after each significant command\n"
            "   - Register output values for post-check verification\n"
            "   - Handle errors with appropriate on_failure (stop/continue)\n\n"

            "4. POST-NODE HEALTH CHECK phase:\n"
            "   - Re-run the same health checks as pre-check\n"
            "   - Compare pre vs post values where applicable\n"
            "   - Verify the activity changes were applied correctly\n"
            "   - Verify node is still healthy after changes\n\n"

            "5. ROLLBACK CONFIGURATION phase:\n"
            "   - Include when condition: '${ROLLBACK_REQUIRED == \"true\"}'\n"
            "   - Reverse the activity changes using backup data\n"
            "   - Verify rollback was successful\n\n"

            "=== NODE CONNECTION RULES ===\n\n"

            "Detect connection type from CIQ columns:\n"
            "- If CIQ has NIAM_NAME/NIAM_ID columns → use NIAM proxy SSH:\n"
            "  node type: remote, plugin: ssh, ne_id: ${niamID}\n"
            "  ssh: { host: ${NIAM_IP}, port: ${M2MPORT}, user: ${M2MUSER}, auth: { password: ${M2MPASSWORD} } }\n"
            "- If CIQ has IP/Host + Username/Password columns → use direct SSH:\n"
            "  node type: remote, plugin: ssh\n"
            "  ssh: { host: ${node_ip}, user: ${node_user}, auth: { password: ${SECRET.node_password} } }\n"
            "- If CIQ has only node names → assume NIAM proxy\n"
            "- Always add a repo_server node for backup/file transfer operations\n\n"

            "=== EXACT STEP STRUCTURE (Java model) ===\n\n"

            "CORRECT step format (follow EXACTLY):\n"
            "  - node: target_node\n"
            "    command_description: Check connectivity\n"
            "    send: hostname\n"
            "    on_failure: stop\n"
            "    register:\n"
            "      - regex: '(?<HOSTNAME>\\S+)'\n"
            "    validation:\n"
            "      enabled: true\n"
            "      description: Verify connectivity\n"
            "      success:\n"
            "        criteria:\n"
            "          expr: '${HOSTNAME != \"\"}'\n"
            "        message: Connected successfully\n"
            "      failure:\n"
            "        message: Failed to connect\n\n"

            "REGISTER: must be a LIST of items with regex or name/value:\n"
            "  register:\n"
            "    - regex: '(?<VAR_NAME>pattern)'\n"
            "    - name: DERIVED\n"
            "      value: '${SOME_VAR}'\n"
            "WRONG register formats: register: {name: regex} or register: name: regex\n\n"

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

            "FORBIDDEN step fields: id, step_id, step_name — use command_description instead\n\n"

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
            "- Use register with (?<NAMED_GROUP>...) regex for every step that captures output\n"
            "- Use validation: { enabled: true, success: { criteria: { expr: ... } } } format\n"
            "- phases MUST be a MAP (key: value), NOT a list\n"
            "- Steps MUST NOT have 'id' field — use command_description instead\n"
            "- Use 2-space indentation consistently, NO tabs\n"
            "- Quote strings with special chars: colons, braces, brackets, #\n"
            "- Multi-line send commands use YAML literal block: send: |\n"
            "- Return ONLY valid YAML, no markdown fences"
        )

        system = (
            "You are a telecom network automation expert for the cr-automation CLI framework. "
            "You generate production-ready workflow YAML v1.2 files that execute MOP procedures "
            "against network nodes via SSH. Your workflows are comprehensive with proper health checks, "
            "backup, activity execution, post-checks, and rollback phases. "
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

    # Fix tabs → 2 spaces
    yaml_text = yaml_text.replace("\t", "  ")

    # Fix trailing whitespace
    yaml_text = "\n".join(line.rstrip() for line in yaml_text.split("\n"))

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

    # Count total steps and validate step fields
    VALID_STEP_FIELDS = {
        "node", "command_description", "send", "on_failure", "timeout_sec",
        "register", "validation", "retries", "retry_delay_sec", "when",
        "skip_when", "expect_reply", "prompt_regex", "for_each", "enabled",
        "interaction_timeout_sec", "name", "description", "steps",
    }
    INVALID_STEP_FIELDS = {"id", "step_id", "step_name"}

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
