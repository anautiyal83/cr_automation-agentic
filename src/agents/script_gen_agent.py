"""Step 6: Script and simulator config generation — generates optional Python scripts + simulator_config.yaml."""
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


class ScriptGenAgent:
    step_number = 6
    step_name = "Script + Simulator Config Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(6, config)

        schema_path = Path("schemas/simulator_config.schema.yaml")
        schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""

        prompt_path = Path("src/prompts/script_gen.yaml")
        prompt_template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        few_shot_text = ""
        if few_shot_examples:
            for ex in few_shot_examples[:3]:
                if "simulator_config" in ex:
                    few_shot_text += (
                        f"\n--- Example from UC '{ex['uc_name']}' ---\n"
                        f"{ex['simulator_config']}\n"
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

        # Generate simulator_config.yaml
        sim_prompt = (
            f"Generate a simulator_config.yaml for this UC:\n\n{ir_summary}\n\n"
            f"Schema reference:\n{schema}\n\n"
            f"{few_shot_text}{retry_info}\n\n"
            "Return ONLY valid YAML content."
        )
        sim_system = (
            "You are a YAML config generator for the cr-automation framework. "
            "Generate simulator_config.yaml conforming exactly to the schema."
        )

        sim_response = await call_llm(sim_prompt, system_prompt=sim_system, model=model)
        sim_yaml = _extract_yaml(sim_response)

        # Generate optional Python scripts
        script_prompt = (
            f"Based on this UC intermediate representation, determine if any custom Python "
            f"scripts are needed for data transformations or validations that cannot be "
            f"expressed in YAML configs.\n\n{ir_summary}\n\n"
            "If scripts are needed, return a JSON object with keys as filenames and values "
            "as the Python code. If no scripts are needed, return an empty JSON object {{}}."
        )
        script_system = (
            "You are a Python script generator for the cr-automation framework. "
            "Only generate scripts when YAML configs are insufficient. Return valid JSON."
        )

        scripts: dict[str, str] = {}
        try:
            script_response = await call_llm(
                script_prompt,
                system_prompt=script_system,
                model=model,
                response_format={"type": "json_object"},
            )
            scripts = json.loads(script_response)
            if not isinstance(scripts, dict):
                scripts = {}
        except Exception as e:
            logger.warning("Script generation LLM call failed: %s", e)

        return StepOutput(
            status="passed",
            output_data={
                "simulator_config_yaml": sim_yaml,
                "scripts": scripts,
            },
            artifacts=[],
        )
