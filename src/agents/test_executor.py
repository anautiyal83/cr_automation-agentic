"""Test execution — invokes ciq-processor and cli-automation-standalone JARs.

Used in two modes:
- mode="ciq"  (Step 5): Test validation-rules.yaml + json-output.yaml via ciq-processor
- mode="exec" (Step 8): Test workflow.yaml + simulator-config.yaml via cli-automation-standalone
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext

logger = logging.getLogger(__name__)


class TestExecutor:
    def __init__(self, mode: str = "ciq"):
        self.mode = mode  # "ciq" or "exec"
        if mode == "ciq":
            self.step_number = 5
            self.step_name = "CIQ Validation Tester"
        else:
            self.step_number = 8
            self.step_name = "Execution Tester"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        if self.mode == "ciq":
            return await self._run_ciq_test(input_data, config)
        else:
            return await self._run_exec_test(input_data, config)

    async def _run_ciq_test(self, input_data: dict, config: Any) -> StepOutput:
        """Full QA cycle for CIQ validation.

        1. Parse validation rules → generate test case register
        2. Generate modified CIQ files for each negative test
        3. Execute each test CIQ against the JAR
        4. Compare expected vs actual → produce QA execution report
        """
        from src.agents.ciq_qa import (
            generate_test_cases,
            execute_test_cases,
            generate_test_case_register,
            generate_execution_report_excel,
            generate_execution_report_html,
        )

        artifacts = input_data.get("artifacts", {})
        run_dir = input_data.get("run_dir", ".")
        uc_name = input_data.get("uc_name", "UC")
        node_type, activity = self._parse_uc_name(uc_name)

        # Save YAML artifacts to files for JAR consumption
        filename_map = {
            "validation_rules_yaml": f"{uc_name}_validation-rules.yaml",
            "json_template_yaml": f"{uc_name}_json-output.yaml",
        }
        for key in ("validation_rules_yaml", "json_template_yaml"):
            content = artifacts.get(key, "")
            if content:
                fname = filename_map[key]
                fpath = Path(run_dir) / fname
                fpath.write_text(content, encoding="utf-8")

        ciq_path = input_data.get("ciq_file_path", "")
        rules_path = str(Path(run_dir) / f"{uc_name}_validation-rules.yaml")
        json_output_path = str(Path(run_dir) / f"{uc_name}_json-output.yaml")
        ciq_jar = self._get_config(config, "ciq_processor_jar")

        # Pre-flight checks
        if not (ciq_jar and Path(ciq_jar).exists()):
            return StepOutput(
                status="failed",
                error={"test_failures": [{"test": "setup", "error": f"JAR not found: {ciq_jar}"}]},
                output_data={"test_cases": [], "artifacts": artifacts},
            )
        if not Path(rules_path).exists():
            return StepOutput(
                status="failed",
                error={"test_failures": [{"test": "setup", "error": "validation-rules.yaml not found"}]},
                output_data={"test_cases": [], "artifacts": artifacts},
            )

        # ── QA directory structure ──
        qa_dir = str(Path(run_dir) / "qa")
        Path(qa_dir).mkdir(exist_ok=True)

        # ── Phase 1: Generate test cases ──
        logger.info("Generating CIQ test cases from validation rules...")
        json_out = json_output_path if Path(json_output_path).exists() else None
        test_cases = generate_test_cases(rules_path, json_out, ciq_path, uc_name)

        # Write test case register
        tc_register_path = str(Path(qa_dir) / "test-cases.xlsx")
        generate_test_case_register(tc_register_path, test_cases, uc_name)
        logger.info("Generated %d test cases → %s", len(test_cases), tc_register_path)

        # ── Phase 2 & 3: Execute test cases ──
        logger.info("Executing %d test cases...", len(test_cases))
        test_cases = await execute_test_cases(
            test_cases=test_cases,
            original_ciq=ciq_path,
            rules_path=rules_path,
            json_output_path=json_out,
            ciq_jar=ciq_jar,
            node_type=node_type,
            activity=activity,
            qa_dir=qa_dir,
        )

        # ── Copy primary reports to ciq_output for downstream steps ──
        main_output = str(Path(run_dir) / "ciq_output")
        Path(main_output).mkdir(exist_ok=True)
        tc01_results = str(Path(qa_dir) / "results" / "TC01")
        if Path(tc01_results).exists():
            for f in Path(tc01_results).iterdir():
                dest = Path(main_output) / f.name
                if not dest.exists():
                    dest.write_bytes(f.read_bytes())

        # ── Phase 4: Generate QA reports ──
        html_path = str(Path(qa_dir) / "qa-execution-report.html")
        excel_path = str(Path(qa_dir) / "qa-execution-report.xlsx")
        generate_execution_report_excel(excel_path, test_cases, uc_name)
        generate_execution_report_html(html_path, test_cases, uc_name, node_type, activity)

        # Determine overall status
        failed_tcs = [tc for tc in test_cases if tc.get("status") == "FAIL"]
        status = "failed" if failed_tcs else "passed"

        return StepOutput(
            status=status,
            error={"test_failures": [
                {"test": tc["id"], "error": tc.get("remarks", "")} for tc in failed_tcs
            ]} if failed_tcs else None,
            output_data={
                "test_cases": test_cases,
                "artifacts": artifacts,
                "report_html": html_path,
                "report_excel": excel_path,
            },
        )


    async def _run_exec_test(self, input_data: dict, config: Any) -> StepOutput:
        """Test workflow.yaml + simulator-config.yaml via cli-automation-standalone JAR."""
        failures: list[dict[str, Any]] = []
        results: dict[str, Any] = {}
        timeout = 300

        artifacts = input_data.get("artifacts", {})
        run_dir = input_data.get("run_dir", ".")
        uc_name = input_data.get("uc_name", "UC")

        # Save workflow and simulator config to files
        filename_map = {
            "workflow_yaml": f"{uc_name}.yaml",
            "simulator_config_yaml": f"{uc_name}_simulator-config.yaml",
        }
        for key in ("workflow_yaml", "simulator_config_yaml"):
            content = artifacts.get(key, "")
            if content:
                fname = filename_map.get(key, f"{uc_name}_{key.replace('_yaml', '')}.yaml")
                fpath = Path(run_dir) / fname
                fpath.write_text(content, encoding="utf-8")

        workflow_path = str(Path(run_dir) / f"{uc_name}.yaml")
        sim_config_path = str(Path(run_dir) / f"{uc_name}_simulator-config.yaml")

        cli_jar = self._get_config(config, "cli_automation_jar")

        if cli_jar and Path(cli_jar).exists() and Path(workflow_path).exists():
            try:
                cmd = ["java", "-jar", cli_jar,
                       "--workflow", workflow_path,
                       "--output", str(Path(run_dir) / "exec_result.json")]
                if Path(sim_config_path).exists():
                    cmd.extend(["--simulator-config", sim_config_path])

                result = await asyncio.to_thread(
                    subprocess.run, cmd,
                    capture_output=True, text=True, timeout=timeout,
                )
                results["execution_test"] = {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                if result.returncode != 0:
                    failures.append({"test": "workflow_execution", "error": result.stderr or "Non-zero exit code"})
            except Exception as e:
                failures.append({"test": "workflow_execution", "error": str(e)})
        else:
            logger.warning("cli-automation JAR not found at: %s — skipping execution tests", cli_jar)

        status = "failed" if failures else "passed"
        return StepOutput(
            status=status,
            error={"test_failures": failures} if failures else None,
            output_data={"results": results, "artifacts": artifacts},
        )

    @staticmethod
    def _parse_uc_name(uc_name: str) -> tuple[str, str]:
        """Extract node_type and activity from UC name.

        UC name format: <NODE_TYPE>_<ACTIVITY_NAME>
        e.g. DSR_1058_IFC_CAPABILITY_MODIFICATION → node_type=DSR, activity=1058_IFC_CAPABILITY_MODIFICATION
        """
        parts = uc_name.split("_", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return uc_name, uc_name

    @staticmethod
    def _get_config(config: Any, key: str) -> str:
        if isinstance(config, dict):
            val = config.get(key, "")
        else:
            val = getattr(config, key, "")
        if val:
            p = Path(val)
            if not p.is_absolute():
                # Resolve relative paths from project root
                project_root = Path(__file__).resolve().parent.parent.parent
                p = project_root / p
            return str(p)
        return val
