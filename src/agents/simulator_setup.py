"""Step 7: Simulator setup — generates MockWire config YAMLs from the workflow.

Parses the workflow YAML to extract:
- Node definitions (SSH connections) → MockWire instances (auth, prompts)
- Send commands per node → command→response mappings (exact or regex)
- Validation patterns → expected success responses

Output structure:
  <run_dir>/simulator/
    config/
      <node_name>.yaml       # MockWire config per node
    data/
      <command_hash>.txt      # Response files for large outputs
    simulator_manifest.yaml   # Instance list with ports
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)

# Default port range for simulator instances
BASE_PORT = 31000


def _extract_yaml(text: str) -> str:
    match = re.search(r'```(?:yaml)?\s*\n(.*?)```', text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


class SimulatorSetupAgent:
    step_number = 8
    step_name = "Simulator Config Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        run_dir = Path(input_data.get("run_dir", "."))
        uc_name = input_data.get("uc_name", "UC")
        artifacts = input_data.get("artifacts", {})

        # Load the workflow YAML from Step 6
        workflow_content = artifacts.get("workflow_yaml", "")
        if not workflow_content:
            wf_path = run_dir / f"{uc_name}.yaml"
            if wf_path.exists():
                workflow_content = wf_path.read_text(encoding="utf-8")

        if not workflow_content:
            return StepOutput(
                status="failed",
                error={"message": "No workflow YAML available from Step 6"},
                output_data={},
            )

        # Parse workflow
        yaml = YAML()
        try:
            workflow = yaml.load(StringIO(workflow_content))
        except Exception as e:
            return StepOutput(
                status="failed",
                error={"message": f"Failed to parse workflow YAML: {e}"},
                output_data={},
            )

        # Extract nodes and commands
        nodes = _extract_nodes(workflow)
        commands_by_node = _extract_commands(workflow)

        # Set up simulator directory
        sim_dir = run_dir / "simulator"
        config_dir = sim_dir / "config"
        data_dir = sim_dir / "data"
        config_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        # Use LLM to generate realistic mock responses for each command
        model = get_model_for_step(7, config)
        mop_data = input_data.get("mop_data", {})
        command_outputs = input_data.get("command_outputs", {})
        exec_feedback = input_data.get("exec_feedback", [])

        # Parse UC name for naming convention
        parts = uc_name.split("_", 1)
        node_type_uc = parts[0] if len(parts) == 2 else uc_name
        activity_uc = parts[1] if len(parts) == 2 else uc_name

        node_configs = {}
        manifest_instances = []
        port = BASE_PORT

        for node_name, node_def in nodes.items():
            ntype = node_def.get("type", "remote")
            if ntype not in ("remote", "local"):
                continue  # skip REST, SFTP, email nodes for now

            node_commands = commands_by_node.get(node_name, [])
            if not node_commands:
                continue

            # Generate mock responses via LLM
            responses = await self._generate_responses(
                node_name, node_def, node_commands,
                mop_data, command_outputs, uc_name, model,
                exec_feedback=exec_feedback,
            )

            # Build MockWire config YAML
            mockwire_config = _build_mockwire_config(
                node_name, node_def, node_commands, responses, data_dir,
            )
            node_configs[node_name] = mockwire_config

            # Write config file: <UC_NAME>_SIMULATOR_CONFIG.yaml
            # Multiple nodes get a suffix: _SIMULATOR_CONFIG_<node_name>.yaml
            ssh_nodes = [n for n, d in nodes.items() if d.get("type", "remote") in ("remote", "local") and commands_by_node.get(n)]
            if len(ssh_nodes) <= 1:
                config_filename = f"{uc_name}_SIMULATOR_CONFIG.yaml"
            else:
                config_filename = f"{uc_name}_SIMULATOR_CONFIG_{node_name}.yaml"
            config_path = config_dir / config_filename
            yaml_out = YAML()
            yaml_out.default_flow_style = False
            with open(str(config_path), "w", encoding="utf-8") as f:
                yaml_out.dump(mockwire_config, f)

            manifest_instances.append({
                "name": node_name,
                "port": port,
                "config": str(config_path),
                "config_filename": config_filename,
                "type": ntype,
            })
            port += 1

        # Write manifest
        manifest = {
            "uc_name": uc_name,
            "instances": manifest_instances,
            "mockwire_jar": "mockwire.jar",
        }
        manifest_path = sim_dir / "simulator_manifest.yaml"
        yaml_out = YAML()
        yaml_out.default_flow_style = False
        with open(str(manifest_path), "w", encoding="utf-8") as f:
            yaml_out.dump(manifest, f)

        # Also produce the legacy simulator_config_yaml artifact
        legacy_content = _build_legacy_config(manifest, node_configs)

        return StepOutput(
            status="passed",
            output_data={
                "simulator_config_yaml": legacy_content,
                "simulator_dir": str(sim_dir),
                "simulator_manifest": str(manifest_path),
                "node_configs": {
                inst["name"]: inst["config"] for inst in manifest_instances
            },
            },
            artifacts=[str(manifest_path)] + [
                inst["config"] for inst in manifest_instances
            ],
        )

    async def _generate_responses(
        self,
        node_name: str,
        node_def: dict,
        commands: list[dict],
        mop_data: dict,
        command_outputs: dict,
        uc_name: str,
        model: str,
        exec_feedback: list[dict] | None = None,
    ) -> dict[str, str]:
        """Use LLM to generate realistic mock responses for each command."""
        cmd_parts = []
        for i, c in enumerate(commands):
            line = f"  {i+1}. [{c.get('phase','')}] {c.get('send','')}"
            # Include register patterns so responses match them
            regs = c.get("register", [])
            if regs and isinstance(regs, list):
                patterns = [r.get("regex", "") for r in regs if isinstance(r, dict) and r.get("regex")]
                if patterns:
                    line += f"\n     Register patterns: {patterns}"
            # Include validation success message for context
            val = c.get("validation", {})
            if isinstance(val, dict):
                success = val.get("success", {})
                if isinstance(success, dict):
                    expr = success.get("criteria", {}).get("expr", "") if isinstance(success.get("criteria"), dict) else ""
                    if expr:
                        line += f"\n     Validation expects: {expr}"
            cmd_parts.append(line)
        cmd_list = "\n".join(cmd_parts)

        prompt = (
            f"Generate realistic mock SSH responses for a Nokia telecom node.\n\n"
            f"UC: {uc_name}\n"
            f"Node: {node_name} (type: {node_def.get('type','remote')})\n\n"
            f"Commands to mock (in execution order):\n{cmd_list}\n\n"
            f"MOP context:\n{json.dumps(mop_data, indent=2, default=str)[:3000]}\n\n"
            f"Command output samples:\n{json.dumps(command_outputs, indent=2, default=str)[:3000]}\n\n"
            "For each command, generate a JSON object where:\n"
            "- key = the exact command string (copy it exactly, including newlines)\n"
            "- value = the mock response text (what the SSH server would return)\n\n"
            "CRITICAL RULES:\n"
            "- Responses MUST match the register regex patterns listed above\n"
            "  e.g. if register is '(?<systemStatus>\\w+)' after 'System Status:', response MUST contain 'System Status: Up'\n"
            "  e.g. if validation expects '${alarmCount == \"0\"}', response must let alarmCount capture '0'\n"
            "- Responses must contain CONCRETE values only — NO ${var} placeholders\n"
            "- Use realistic sample values: real IPs (10.71.146.15), hostnames (DSR-APP-01), etc.\n"
            "- Response is ONLY what the server prints AFTER the command — do NOT echo the command back\n"
            "- For 'echo SUCCESS' commands, respond with just 'SUCCESS'\n"
            "- For 'date' commands, respond with '2026-10-14_12-30-00'\n"
            "- For 'whoami' commands, respond with 'root' or 'admin'\n"
            "- For configuration/save commands, respond with 'OK' or 'Configuration saved successfully.'\n"
            "- For show/display commands, respond with realistic telecom output using concrete values\n"
            "- For sha256sum commands, respond with a 64-char hex hash\n"
            "- Keep responses concise (1-5 lines typically)\n\n"
        )

        # Add execution failure feedback from previous Step 9 run
        if exec_feedback:
            feedback_text = json.dumps(exec_feedback, indent=2, default=str)[:3000]
            prompt += (
                "=== PREVIOUS EXECUTION FAILURES (fix these!) ===\n"
                "The following commands FAILED in the previous execution run.\n"
                "Your responses MUST fix these failures — generate responses that:\n"
                "1. Match the register regex patterns so variables are captured\n"
                "2. Satisfy the validation criteria so the step passes\n"
                "3. Contain realistic values that the validation expression expects\n\n"
                f"{feedback_text}\n\n"
            )

        prompt += "Return ONLY a valid JSON object, no markdown."

        try:
            response = await call_llm(
                prompt,
                system_prompt="You are a Nokia telecom equipment simulator. Generate realistic mock SSH command responses.",
                model=model,
            )
            # Extract JSON
            response = response.strip()
            if response.startswith("```"):
                match = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
                response = match.group(1) if match else response
            return json.loads(response)
        except Exception as e:
            logger.warning("LLM response generation failed for %s: %s", node_name, e)
            # Fallback: generate simple responses
            return {c.get("send", ""): "OK" for c in commands}


def _short_filename(description: str, seen: set[str]) -> str:
    """Build a short underscore-separated filename from a description.

    e.g. "Verify IFC Capability existence" → "verify_ifc_capability"
    Ensures uniqueness by appending _2, _3, etc.
    """
    # Take first 4 significant words, lowercase, underscored
    words = re.sub(r'[^a-zA-Z0-9\s]', '', description).split()
    short = "_".join(w.lower() for w in words[:4]) or "response"
    short = short[:40]  # cap length
    base = short
    n = 1
    while short in seen:
        n += 1
        short = f"{base}_{n}"
    seen.add(short)
    return short


def _extract_nodes(workflow: dict) -> dict[str, dict]:
    """Extract node definitions from the workflow."""
    return workflow.get("nodes", {}) or {}


def _extract_commands(workflow: dict) -> dict[str, list[dict]]:
    """Extract all send commands grouped by target node."""
    commands: dict[str, list[dict]] = {}

    def _process_steps(steps: list, phase_name: str = ""):
        if not steps:
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            node = step.get("node", "")
            send = step.get("send", "")
            if node and send:
                commands.setdefault(node, []).append({
                    "send": send.strip(),
                    "phase": phase_name,
                    "description": step.get("command_description", step.get("name", "")),
                    "validation": step.get("validation", {}),
                    "register": step.get("register", []),
                })
            # Handle nested steps (for_each, etc.)
            if "steps" in step:
                _process_steps(step["steps"], phase_name)

    # Process phases
    phases = workflow.get("phases", {})
    if isinstance(phases, dict):
        for phase_name, phase_def in phases.items():
            if isinstance(phase_def, dict):
                _process_steps(phase_def.get("steps", []), phase_name)
    elif isinstance(phases, list):
        for phase_def in phases:
            if isinstance(phase_def, dict):
                pname = phase_def.get("name", "")
                _process_steps(phase_def.get("steps", []), pname)

    # Process top-level steps
    _process_steps(workflow.get("steps", []), "main")

    # Process pre_execution
    globals_def = workflow.get("globals", {}) or {}
    _process_steps(globals_def.get("pre_execution", []), "pre_execution")

    return commands


def _build_mockwire_config(
    node_name: str,
    node_def: dict,
    commands: list[dict],
    responses: dict[str, str],
    data_dir: Path,
) -> dict:
    """Build a MockWire YAML config for a node."""
    # Always use 'automation-user' — the test executor patches workflow
    # SSH credentials to this same username for simulator testing
    user = "automation-user"
    prompt = f"[{user}@{node_name} ~]$ "

    config: dict[str, Any] = {
        "config": {
            "prompt": "#",
            "delayMs": 100,
            "jitterMs": 20,
            "idleTimeoutSec": 300,
        },
        "auth": {
            "enabled": True,
            "users": [{
                "username": user,
                "password": "automation-pass",
                "prompt": prompt,
            }],
        },
    }

    # Add NIAM config if ne_id is present
    ssh_def = node_def.get("ssh", {}) or {}
    ne_id = ssh_def.get("ne_id", "")
    if ne_id:
        config["niam"] = {
            "enabled": True,
            "prompts": {
                "passwordPrompt": "Password: ",
                "neIdPrompt": "neId: ",
            },
            "users": [{
                "username": user,
                "password": "password",
                "neIds": [ne_id if not ne_id.startswith("${") else f"{node_name}-NIAM-ID"],
            }],
        }

    # Command mappings — track used keys to avoid duplicates
    seen_names: set[str] = set()
    used_keys: set[str] = set()
    for cmd_info in commands:
        send = cmd_info["send"]
        response_text = responses.get(send, "OK")
        desc = cmd_info.get("description", "")

        # Build a unique command key for MockWire matching
        lines = [l.strip() for l in send.strip().split("\n") if l.strip()]

        if len(lines) <= 1:
            # Single-line command — strip pipe suffix since MockWire handles pipes internally
            cmd_key = lines[0] if lines else send.strip()
            if " | " in cmd_key:
                cmd_key = cmd_key.split(" | ")[0].strip()
        else:
            # Multi-line (heredoc) command — extract significant inner lines
            filler = {"EOF", "EXIT", "SAVE", "END", "QUIT", "BYE", "CONFIG", "COMMIT"}
            inner = [l for l in lines[1:] if l.upper() not in filler]
            if not inner:
                inner = lines[1:2]  # fallback to second line
            # Join up to 2 inner lines with "; " for a unique key
            cmd_key = "; ".join(inner[:2])

        # Determine if command needs regex matching (contains variables or special chars)
        if "${" in cmd_key or any(c in cmd_key for c in "*+?[](){}|\\"):
            escaped = re.escape(cmd_key)
            escaped = re.sub(r'\\\$\\\{[^}]+\\\}', r'\\S+', escaped)
            key = f"regex:{escaped}"
        else:
            key = cmd_key

        # Deduplicate keys — if same key already used, suffix with description
        if key in used_keys and desc:
            safe_desc = re.sub(r'[^a-zA-Z0-9_]', '_', desc)[:30].strip('_')
            key = f"{key} # {safe_desc}"
        used_keys.add(key)

        # Quote the key for YAML safety — colons in regex patterns break YAML parsing
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString
        key = DoubleQuotedScalarString(key)

        # Store response
        if len(response_text) > 200 or "\n" in response_text:
            # Build short filename from description or command
            base = desc or send.split()[0] if send else "response"
            short = _short_filename(base, seen_names)
            resp_file = data_dir / f"{short}.txt"
            resp_file.write_text(response_text, encoding="utf-8")
            config[key] = {"file": str(resp_file.name)}
        else:
            lines = [l for l in response_text.strip().split("\n")]
            config[key] = {"lines": lines}

    return config


def _build_legacy_config(manifest: dict, node_configs: dict) -> str:
    """Build a legacy simulator_config_yaml string for backward compatibility."""
    yaml = YAML()
    yaml.default_flow_style = False
    legacy = {
        "uc_name": manifest["uc_name"],
        "simulator_type": "mockwire",
        "instances": manifest["instances"],
        "nodes": list(node_configs.keys()),
    }
    buf = StringIO()
    yaml.dump(legacy, buf)
    return buf.getvalue()
