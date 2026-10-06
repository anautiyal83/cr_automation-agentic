"""Step 8: Test execution — invokes ciq-processor and cli-automation-standalone JARs."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


class TestExecutor:
    step_number = 8
    step_name = "Test Executor"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        results: dict[str, Any] = {"validation_test": None, "workflow_test": None}
        failures: list[dict[str, Any]] = []
        timeout = 300

        artifacts = input_data.get("artifacts", {})
        run_dir = input_data.get("run_dir", ".")

        # Save YAML artifacts to files for JAR consumption
        for key, content in artifacts.items():
            if content and key.endswith("_yaml"):
                fpath = Path(run_dir) / f"{key.replace('_yaml', '')}.yaml"
                fpath.write_text(content, encoding="utf-8")

        ciq_path = input_data.get("ciq_file_path", "")
        rules_path = str(Path(run_dir) / "validation_rules.yaml")

        # Run CIQ validation test via ciq-processor JAR
        ciq_jar = (
            getattr(config, "ciq_processor_jar", config.get("ciq_processor_jar", ""))
            if isinstance(config, dict)
            else getattr(config, "ciq_processor_jar", "")
        )

        if ciq_jar and Path(ciq_jar).exists() and Path(rules_path).exists():
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "java", "-jar", ciq_jar,
                        "--input", ciq_path,
                        "--rules", rules_path,
                        "--output", str(Path(run_dir) / "ciq_result.json"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                results["validation_test"] = {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                if result.returncode != 0:
                    failures.append({
                        "test": "ciq_validation",
                        "error": result.stderr or "Non-zero exit code",
                    })
            except Exception as e:
                failures.append({"test": "ciq_validation", "error": str(e)})

        # Run workflow test via cli-automation-standalone JAR
        cli_jar = (
            config.get("cli_automation_jar", "")
            if isinstance(config, dict)
            else getattr(config, "cli_automation_jar", "")
        )
        workflow_path = str(Path(run_dir) / "workflow.yaml")
        sim_config_path = str(Path(run_dir) / "simulator_config.yaml")

        if cli_jar and Path(cli_jar).exists() and Path(workflow_path).exists():
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "java", "-jar", cli_jar,
                        "--workflow", workflow_path,
                        "--simulator-config", sim_config_path,
                        "--output", str(Path(run_dir) / "workflow_result.json"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                results["workflow_test"] = {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                if result.returncode != 0:
                    failures.append({
                        "test": "workflow_execution",
                        "error": result.stderr or "Non-zero exit code",
                    })
            except Exception as e:
                failures.append({"test": "workflow_execution", "error": str(e)})

        if failures:
            return StepOutput(
                status="failed",
                error={"test_failures": failures},
                output_data={"results": results, "artifacts": artifacts},
            )
        return StepOutput(
            status="passed",
            output_data={"results": results, "artifacts": artifacts},
        )
