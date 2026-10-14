"""Step 5: Workflow YAML generation."""
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

        prompt_path = Path("src/prompts/workflow_gen.yaml")
        prompt_template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        few_shot_text = ""
        if few_shot_examples:
            for ex in few_shot_examples[:3]:
                if "workflow" in ex:
                    few_shot_text += (
                        f"\n--- Example from UC '{ex['uc_name']}' ---\n"
                        f"{ex['workflow']}\n"
                    )

        ir_summary = json.dumps(
            {k: input_data.get(k) for k in ("uc_metadata", "mop_data", "command_outputs")},
            indent=2,
            default=str,
        )[:6000]

        retry_info = ""
        if retry_context:
            retry_info = (
                f"\n\nPREVIOUS ATTEMPT FAILED:\n"
                f"{json.dumps(retry_context.previous_error, indent=2)}\n"
                "Fix the issues and regenerate."
            )

        uc_name = input_data.get("uc_name", "UC")
        prompt = (
            f"Generate a CLI automation workflow YAML for UC: {uc_name}\n\n"
            f"Input documents data (MOP steps and command outputs):\n{ir_summary}\n\n"
            f"cr-automation workflow schema reference (v1.2):\n{schema[:12000]}\n\n"
            f"{few_shot_text}{retry_info}\n\n"
            "IMPORTANT: Generate a COMPLETE workflow YAML that follows the cr-automation CLI automation schema v1.2.\n"
            "Include: version, name, globals (vars with ${var} placeholders for CIQ data, secrets, defaults), "
            "nodes (SSH remote connections with host/user/auth from CIQ variables), "
            "and phases with steps (send commands from MOP, register output variables, "
            "add validation blocks with success/failure criteria).\n"
            "Use ${varName} syntax for variable interpolation.\n"
            "Map MOP commands to workflow steps, use command outputs for validation patterns.\n"
            "Return ONLY valid YAML content, no markdown fences."
        )
        system = (
            "You are a telecom UC config generator for the cr-automation CLI automation framework. "
            "Generate workflow YAML v1.2 with globals, nodes (SSH/REST), phases, steps with "
            "send commands, register variables, validation, and expect_reply blocks."
        )

        response = await call_llm(prompt, system_prompt=system, model=model)
        yaml_content = _extract_yaml(response)

        return StepOutput(
            status="passed",
            output_data={"workflow_yaml": yaml_content},
            artifacts=[],
        )
