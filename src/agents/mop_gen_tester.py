"""Step 7: MOP Generation Test — generates MOP HTML from workflow YAML + JSON data.

Uses cli-automation-standalone.jar in 'generate' mode to validate that the
workflow YAML can produce a proper MOP document.

CLI: java -jar cli-automation-standalone.jar generate
     --json <input.json> --schema <json-output.yaml>
     --yaml <workflow.yaml> --activity <name> --cr <cr>
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext

logger = logging.getLogger(__name__)


class MopGenTester:
    step_number = 7
    step_name = "MOP Generation Tester"

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
        timeout = 300

        parts = uc_name.split("_", 1)
        node_type = parts[0] if len(parts) == 2 else uc_name
        activity = parts[1] if len(parts) == 2 else uc_name

        # Locate required files
        workflow_path = run_dir / f"{uc_name}.yaml"
        json_output_schema = run_dir / f"{uc_name}_json-output.yaml"
        json_input = run_dir / "ciq_output" / f"{uc_name}.json"

        # Write artifacts to disk if not already present
        if not workflow_path.exists() and artifacts.get("workflow_yaml"):
            workflow_path.write_text(artifacts["workflow_yaml"], encoding="utf-8")
        if not json_output_schema.exists() and artifacts.get("json_template_yaml"):
            json_output_schema.write_text(artifacts["json_template_yaml"], encoding="utf-8")

        # Pre-flight checks
        missing = []
        if not workflow_path.exists():
            missing.append(f"Workflow YAML: {workflow_path}")
        if not json_output_schema.exists():
            missing.append(f"JSON output schema: {json_output_schema}")
        if not json_input.exists():
            missing.append(f"JSON input data: {json_input}")
        cli_jar = self._get_config(config, "cli_automation_jar")
        if not (cli_jar and Path(cli_jar).exists()):
            missing.append(f"cli-automation JAR: {cli_jar}")

        if missing:
            return StepOutput(
                status="failed",
                error={"message": "Missing files:\n" + "\n".join(missing)},
                output_data={"artifacts": artifacts},
            )

        # Extract CR groups from JSON for --cr argument
        try:
            json_data = json.loads(json_input.read_text(encoding="utf-8"))
            cr_groups = [cg.get("crGroup", "") for cg in json_data.get("crGroups", [])]
        except Exception:
            cr_groups = []
        cr_name = cr_groups[0] if cr_groups else "CR1"

        # MOP output directory
        mop_dir = run_dir / "mop_output"
        mop_dir.mkdir(exist_ok=True)

        # Run MOP generation
        cmd = [
            "java", "-jar", cli_jar,
            "generate",
            "--json", str(json_input),
            "--schema", str(json_output_schema),
            "--yaml", str(workflow_path),
            "--activity", uc_name,
            "--cr", cr_name,
            "--output-dir", str(mop_dir),
        ]

        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return StepOutput(
                status="failed",
                error={"message": f"MOP generation timed out after {timeout}s"},
                output_data={"artifacts": artifacts},
            )
        except Exception as e:
            return StepOutput(
                status="failed",
                error={"message": f"MOP generation failed: {e}"},
                output_data={"artifacts": artifacts},
            )

        # Check results
        mop_files = list(mop_dir.glob("*.html"))
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            return StepOutput(
                status="failed",
                error={
                    "message": f"MOP generation failed (exit code {result.returncode})",
                    "stdout": stdout[:1000],
                    "stderr": stderr[:1000],
                },
                output_data={
                    "artifacts": artifacts,
                    "mop_dir": str(mop_dir),
                },
            )

        # Success — build report
        report = {
            "status": "PASSED",
            "mop_files": [f.name for f in mop_files],
            "mop_dir": str(mop_dir),
            "cr_group": cr_name,
            "stdout": stdout,
        }

        # Generate HTML overview for milestone review
        html_path = str(run_dir / f"{uc_name}_mop-generation-report.html")
        self._generate_report_html(html_path, report, uc_name, mop_files)

        return StepOutput(
            status="passed",
            output_data={
                "artifacts": artifacts,
                "mop_report": report,
                "report_html": html_path,
                "mop_dir": str(mop_dir),
            },
        )

    def _generate_report_html(
        self, filepath: str, report: dict, uc_name: str, mop_files: list[Path],
    ) -> None:
        """Generate HTML report for MOP generation milestone review."""
        import html as html_mod

        files_html = ""
        for f in mop_files:
            size_kb = f.stat().st_size / 1024
            files_html += (
                f'<div class="file-card">'
                f'<span class="file-icon">&#128196;</span>'
                f'<span class="file-name">{html_mod.escape(f.name)}</span>'
                f'<span class="file-size">{size_kb:.1f} KB</span>'
                f'</div>'
            )
        if not files_html:
            files_html = '<div class="file-card"><span class="file-name">No MOP files generated</span></div>'

        stdout = html_mod.escape(report.get("stdout", ""))

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>MOP Generation Report — {html_mod.escape(uc_name)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #333; font-size: 13px; }}
.header {{ background: #1a3a5c; color: #fff; padding: 20px 30px; }}
.header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
.header .meta {{ font-size: 12px; color: #b0c4d8; }}
.summary {{ padding: 16px 24px; margin: 16px 24px; border-radius: 8px; border-left: 6px solid #27ae60;
  background: #eafaf1; display: flex; align-items: center; gap: 20px; }}
.summary .status {{ font-size: 28px; font-weight: 700; color: #27ae60; }}
.stats {{ display: flex; gap: 20px; }}
.stat {{ text-align: center; }}
.stat-num {{ font-size: 20px; font-weight: 700; color: #1a3a5c; }}
.stat-label {{ font-size: 10px; text-transform: uppercase; color: #888; }}
.section {{ margin: 12px 24px; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; padding: 16px; }}
.section h2 {{ font-size: 14px; font-weight: 600; color: #1a3a5c; margin-bottom: 12px; }}
.file-card {{ display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: #f8f9fa; border-radius: 6px; margin-bottom: 6px; }}
.file-icon {{ font-size: 20px; }}
.file-name {{ font-weight: 600; font-size: 13px; flex: 1; }}
.file-size {{ color: #888; font-size: 12px; }}
pre {{ background: #f8f9fa; padding: 10px 14px; border-radius: 4px; font-size: 12px;
  overflow-x: auto; white-space: pre-wrap; word-break: break-word; }}
.footer {{ padding: 12px 30px; font-size: 11px; color: #999; text-align: center; margin-top: 12px; }}
</style></head>
<body>
<div class="header">
  <h1>MOP Generation Report</h1>
  <div class="meta">UC: {html_mod.escape(uc_name)} | CR: {html_mod.escape(report.get('cr_group', ''))}</div>
</div>
<div class="summary">
  <div class="status">PASSED</div>
  <div class="stats">
    <div class="stat"><div class="stat-num">{len(mop_files)}</div><div class="stat-label">MOP Files</div></div>
  </div>
</div>
<div class="section">
  <h2>Generated MOP Documents</h2>
  {files_html}
</div>
{'<div class="section"><h2>Generation Output</h2><pre>' + stdout + '</pre></div>' if stdout else ''}
<div class="footer">Generated by CR Automation Pipeline — Step 7: MOP Generation Test</div>
</body></html>"""

        Path(filepath).write_text(html, encoding="utf-8")

    @staticmethod
    def _get_config(config: Any, key: str) -> str:
        if isinstance(config, dict):
            val = config.get(key, "")
        else:
            val = getattr(config, key, "")
        if val:
            p = Path(val)
            if not p.is_absolute():
                project_root = Path(__file__).resolve().parent.parent.parent
                p = project_root / p
            return str(p)
        return val
