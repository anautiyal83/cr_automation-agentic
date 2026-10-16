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
            self.step_number = 9
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

        # ── Copy reports + JSON output to ciq_output for downstream steps ──
        main_output = str(Path(run_dir) / "ciq_output")
        Path(main_output).mkdir(exist_ok=True)
        # Copy from TC01 (validation reports) and TC02 (JSON output + reports)
        for tc_name in ("TC01", "TC02"):
            tc_results = Path(qa_dir) / "results" / tc_name
            if tc_results.exists():
                for f in tc_results.iterdir():
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
        """Test workflow execution via cli-automation-standalone JAR.

        CLI: java -jar cli-automation-standalone.jar execute
             --json <json> --schema <schema> --yaml <workflow>
             --node <node> --activity <name> [--cr <cr>] [--output-dir <dir>]
        """
        failures: list[dict[str, Any]] = []
        results: dict[str, Any] = {}
        timeout = 300

        artifacts = input_data.get("artifacts", {})
        run_dir = Path(input_data.get("run_dir", "."))
        uc_name = input_data.get("uc_name", "UC")

        # Save workflow and simulator config to files
        for key, fname in (
            ("workflow_yaml", f"{uc_name}.yaml"),
            ("simulator_config_yaml", f"{uc_name}_simulator-config.yaml"),
            ("json_template_yaml", f"{uc_name}_json-output.yaml"),
        ):
            content = artifacts.get(key, "")
            if content:
                fpath = run_dir / fname
                if not fpath.exists():
                    fpath.write_text(content, encoding="utf-8")

        workflow_path = run_dir / f"{uc_name}.yaml"
        json_schema = run_dir / f"{uc_name}_json-output.yaml"
        json_input = run_dir / "ciq_output" / f"{uc_name}.json"
        exec_output_dir = run_dir / "exec_output"
        exec_output_dir.mkdir(exist_ok=True)

        cli_jar = self._get_config(config, "cli_automation_jar")

        # Pre-flight checks
        missing = []
        if not (cli_jar and Path(cli_jar).exists()):
            missing.append(f"JAR not found: {cli_jar}")
        if not workflow_path.exists():
            missing.append(f"Workflow: {workflow_path}")
        if not json_input.exists():
            missing.append(f"JSON input: {json_input}")
        if not json_schema.exists():
            missing.append(f"JSON schema: {json_schema}")

        if missing:
            return StepOutput(
                status="failed",
                error={"test_failures": [{"test": "setup", "error": "\n".join(missing)}]},
                output_data={"results": {}, "artifacts": artifacts},
            )

        # Extract first node name from JSON for --node arg
        try:
            json_data = json.loads(json_input.read_text(encoding="utf-8"))
            cr_groups = json_data.get("crGroups", [])
            first_cr = cr_groups[0].get("crGroup", "CR1") if cr_groups else "CR1"
            # Get first node
            first_node = ""
            for cg in cr_groups:
                for ng in cg.get("nodeGroups", []):
                    for n in ng.get("nodes", []):
                        first_node = n.get("node", "")
                        if first_node:
                            break
                    if first_node:
                        break
                if first_node:
                    break
            if not first_node:
                first_node = "node-1"
        except Exception:
            first_cr = "CR1"
            first_node = "node-1"

        # Build execution command
        cmd = [
            "java", "-jar", cli_jar,
            "execute",
            "--json", str(json_input),
            "--schema", str(json_schema),
            "--yaml", str(workflow_path),
            "--node", first_node,
            "--activity", uc_name,
            "--cr", first_cr,
            "--output-dir", str(exec_output_dir),
        ]

        try:
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
                failures.append({
                    "test": "workflow_execution",
                    "error": (result.stderr or result.stdout or "Non-zero exit code")[:500],
                })
        except subprocess.TimeoutExpired:
            failures.append({"test": "workflow_execution", "error": f"Timeout after {timeout}s"})
        except Exception as e:
            failures.append({"test": "workflow_execution", "error": str(e)})

        # ── Generate execution report via execution-report-generator JAR ──
        report_html = ""
        summary_html = ""
        report_jar = self._get_config(config, "execution_report_jar")
        if report_jar and Path(report_jar).exists():
            node_type, activity = self._parse_uc_name(uc_name)
            report_dir = run_dir / "exec_report"
            report_dir.mkdir(exist_ok=True)

            report_cmd = [
                "java", "-jar", report_jar,
                "--node-type", node_type,
                "--activity", activity,
                "--cr-group", first_cr,
                "--json-dir", str(exec_output_dir),
                "--output-html-path", str(report_dir),
                "--output-html-name", f"{uc_name}_execution-report.html",
                "--output-summary-name", f"{uc_name}_execution-summary.html",
                "--request-id", first_cr,
            ]

            # Use custom templates from config if available
            exec_summary_tpl = self._get_config(config, "exec_summary_template")
            if exec_summary_tpl and Path(exec_summary_tpl).exists():
                report_cmd.extend(["--summary-template", exec_summary_tpl])
            exec_failure_tpl = self._get_config(config, "exec_failure_template")
            if exec_failure_tpl and Path(exec_failure_tpl).exists():
                report_cmd.extend(["--template", exec_failure_tpl])

            try:
                rpt_result = await asyncio.to_thread(
                    subprocess.run, report_cmd,
                    capture_output=True, text=True, timeout=timeout,
                )
                results["report_generation"] = {
                    "returncode": rpt_result.returncode,
                    "stdout": rpt_result.stdout,
                    "stderr": rpt_result.stderr,
                }
                if rpt_result.returncode == 0:
                    report_html = str(report_dir / f"{uc_name}_execution-report.html")
                    summary_html = str(report_dir / f"{uc_name}_execution-summary.html")
                else:
                    logger.warning("Execution report generation failed: %s",
                                   (rpt_result.stderr or rpt_result.stdout)[:500])
            except subprocess.TimeoutExpired:
                logger.warning("Execution report generation timed out after %ds", timeout)
            except Exception as e:
                logger.warning("Execution report generation error: %s", e)
        else:
            logger.info("Skipping execution report generation — JAR not found: %s", report_jar)

        status = "failed" if failures else "passed"
        output_data: dict[str, Any] = {"results": results, "artifacts": artifacts}
        if report_html:
            output_data["report_html"] = report_html
        if summary_html:
            output_data["summary_html"] = summary_html

        # Collect generated report files as step artifacts
        step_artifacts: list[str] = []
        if report_html and Path(report_html).exists():
            step_artifacts.append(report_html)
        if summary_html and Path(summary_html).exists():
            step_artifacts.append(summary_html)

        return StepOutput(
            status=status,
            artifacts=step_artifacts,
            error={"test_failures": failures} if failures else None,
            output_data=output_data,
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
