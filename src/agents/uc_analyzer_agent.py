"""Step 2: UC requirements analysis — enriches intermediate representation."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


class UCAnalyzerAgent:
    step_number = 2
    step_name = "UC Analyzer"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        model = get_model_for_step(2, config)
        ir_data = input_data.get("intermediate_repr", input_data)

        prompt = (
            "Analyze this UC intermediate representation and enrich it:\n\n"
            f"{json.dumps(ir_data, indent=2)[:8000]}\n\n"
            "For each CIQ field, confirm or refine the field type and validation rule. "
            "Flag fields that need human clarification."
        )

        system = (
            "You are a telecom UC analyst. Enrich the intermediate representation "
            "with validated field types. Return JSON with the enriched data."
        )

        try:
            response = await call_llm(
                prompt,
                system_prompt=system,
                model=model,
                response_format={"type": "json_object"},
            )
            enriched = json.loads(response)
            ir_data.update(enriched)
        except Exception as e:
            logger.warning("LLM enrichment failed, using parser-inferred types: %s", e)

        # Check for fields needing HIL
        hil_requests: list[dict] = []
        field_types = ir_data.get("ciq_data", {}).get("field_types", {})
        for field_name, info in field_types.items():
            if (
                isinstance(info, dict)
                and not info.get("hil_confirmed")
                and info.get("type")
                not in ("string", "integer", "float", "email", "hostname", "ip_address")
            ):
                hil_requests.append({
                    "field_or_section": field_name,
                    "agent_inference": f"Detected type: {info.get('type', 'unknown')}",
                    "question": f"What validation rule should apply to field '{field_name}'?",
                })

        status = "needs_hil" if hil_requests else "passed"
        return StepOutput(
            status=status,
            output_data=ir_data,
            hil_requests=hil_requests if hil_requests else None,
        )
