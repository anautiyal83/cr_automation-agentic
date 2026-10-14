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

        # Validate each YAML artifact is parseable and has expected top-level structure
        expected_keys = {
            "validation_rules_yaml": ["sheets"],
            "json_template_yaml": ["data"],
            "workflow_yaml": ["phases", "steps"],  # one of these
            "simulator_config_yaml": ["mock_entries"],
        }

        for artifact_key, required_keys in expected_keys.items():
            content = artifacts.get(artifact_key, "")
            if not content:
                continue
            try:
                doc = yaml.load(StringIO(content))
                if doc is None:
                    errors.append({"artifact": artifact_key, "error": "YAML parsed as empty/null"})
                elif isinstance(doc, dict):
                    has_any = any(k in doc for k in required_keys)
                    if not has_any:
                        errors.append({
                            "artifact": artifact_key,
                            "error": f"Missing expected key(s): {required_keys}. Found: {list(doc.keys())[:10]}",
                        })
            except Exception as e:
                errors.append({"artifact": artifact_key, "error": f"YAML parse error: {e}"})

        if errors:
            logger.warning("Schema validation found %d issue(s): %s", len(errors),
                           "; ".join(e["artifact"] + ": " + e["error"][:80] for e in errors))
            return StepOutput(
                status="passed",
                output_data={**artifacts, "schema_warnings": errors},
            )
        return StepOutput(status="passed", output_data=artifacts)
