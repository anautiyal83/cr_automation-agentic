"""Step 7: Schema validation — deterministic, no LLM."""
from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

import jsonschema
from ruamel.yaml import YAML

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


class SchemaValidator:
    step_number = 7
    step_name = "Schema Validator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        yaml = YAML()
        errors: list[dict[str, Any]] = []
        artifacts = input_data.get("artifacts", {})

        schema_map = {
            "validation_rules_yaml": "schemas/validation_rules.schema.yaml",
            "json_template_yaml": "schemas/json_template.schema.yaml",
            "workflow_yaml": "schemas/workflow.schema.yaml",
            "simulator_config_yaml": "schemas/simulator_config.schema.yaml",
        }

        for artifact_key, schema_path in schema_map.items():
            content = artifacts.get(artifact_key, "")
            if not content:
                continue
            try:
                doc = yaml.load(StringIO(content))
                schema_doc = yaml.load(Path(schema_path))
                jsonschema.validate(doc, schema_doc)
            except jsonschema.ValidationError as e:
                errors.append({
                    "artifact": artifact_key,
                    "error": str(e.message),
                    "path": list(e.absolute_path),
                })
            except Exception as e:
                errors.append({
                    "artifact": artifact_key,
                    "error": f"Parse error: {e}",
                })

        if errors:
            return StepOutput(
                status="failed",
                error={"validation_errors": errors},
                output_data=artifacts,
            )
        return StepOutput(status="passed", output_data=artifacts)
