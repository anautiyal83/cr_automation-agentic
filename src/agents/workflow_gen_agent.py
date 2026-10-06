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
    step_number = 5
    step_name = "Workflow Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(5, config)

        schema_path = Path("schemas/workflow.schema.yaml")
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

        prompt = (
            f"Generate a workflow.yaml for this UC:\n\n{ir_summary}\n\n"
            f"Schema reference:\n{schema}\n\n"
            f"{few_shot_text}{retry_info}\n\n"
            "Return ONLY valid YAML content."
        )
        system = (
            "You are a YAML config generator for the cr-automation framework. "
            "Generate workflow.yaml conforming exactly to the schema."
        )

        response = await call_llm(prompt, system_prompt=system, model=model)
        yaml_content = _extract_yaml(response)

        return StepOutput(
            status="passed",
            output_data={"workflow_yaml": yaml_content},
            artifacts=[],
        )
