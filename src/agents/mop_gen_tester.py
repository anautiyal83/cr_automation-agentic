"""Step 7: MOP Generation Test — generates MOP HTML from workflow YAML + JSON data.

Generates MOPs for multiple scenarios:
- Each CRGroup
- Each Node within each NodeGroup
Validates workflow can produce proper MOP documents for all combinations.

CLI: java -jar cli-automation-standalone.jar generate
     --json <input.json> --schema <json-output.yaml>
     --yaml <workflow.yaml> --activity <name> --cr <cr>
     [--node <node>] [--output-dir <dir>]
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

        # Locate required files
        workflow_path = run_dir / f"{uc_name}.yaml"
        json_output_schema = run_dir / f"{uc_name}_json-output.yaml"
        json_input = run_dir / "ciq_output" / f"{uc_name}.json"

        # Write artifacts to disk if not already present
        if not workflow_path.exists() and artifacts.get("workflow_yaml"):
            workflow_path.write_text(artifacts["workflow_yaml"], encoding="utf-8")
        if not json_output_schema.exists() and artifacts.get("json_template_yaml"):
            json_output_schema.write_text(artifacts["json_template_yaml"], encoding="utf-8")

        # Fallback: find JSON input from QA results if not in ciq_output
        if not json_input.exists():
            for fallback in [
                run_dir / "qa" / "results" / "TC02" / f"{uc_name}.json",
                *run_dir.rglob(f"{uc_name}.json"),
            ]:
                if fallback.exists():
                    json_input.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(str(fallback), str(json_input))
                    break

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

        # Parse JSON to discover all scenarios
        try:
            json_data = json.loads(json_input.read_text(encoding="utf-8"))
        except Exception as e:
            return StepOutput(
                status="failed",
                error={"message": f"Failed to parse JSON input: {e}"},
                output_data={"artifacts": artifacts},
            )

        scenarios = self._build_scenarios(json_data)
        if not scenarios:
            scenarios = [{"cr": "CR1", "label": "Default"}]

        # MOP output directory
        mop_dir = run_dir / "mop_output"
        mop_dir.mkdir(exist_ok=True)

        # Generate MOP for each scenario
        results: list[dict[str, Any]] = []
        all_mop_files: list[Path] = []

        for sc in scenarios:
            sc_label = sc["label"]
            sc_dir = mop_dir / sc_label.replace(" ", "_").replace("/", "_")
            sc_dir.mkdir(exist_ok=True)

            # Build output filename
            safe_label = sc_label.replace(" ", "_").replace("/", "_").replace("=", "")[:40]
            output_file = sc_dir / f"{uc_name}_MOP_{safe_label}.html"

            # HTML template from config
            html_template = self._get_config(config, "mop_template")

            cmd = [
                "java", "-jar", cli_jar,
                "generate",
                "--json", str(json_input),
                "--schema", str(json_output_schema),
                "--yaml", str(workflow_path),
                "--activity", uc_name,
                "--cr", sc["cr"],
                "--output", str(output_file),
            ]
            if html_template:
                cmd.extend(["--html-template", html_template])
            if sc.get("node"):
                cmd.extend(["--node", sc["node"]])

            try:
                result = await asyncio.to_thread(
                    subprocess.run, cmd,
                    capture_output=True, text=True, timeout=timeout,
                )
                mop_files = list(sc_dir.glob("*.html"))
                all_mop_files.extend(mop_files)
                results.append({
                    "scenario": sc_label,
                    "cr": sc["cr"],
                    "node": sc.get("node", "all"),
                    "status": "PASSED" if result.returncode == 0 else "FAILED",
                    "returncode": result.returncode,
                    "mop_files": [f.name for f in mop_files],
                    "stdout": result.stdout.strip()[:500],
                    "stderr": result.stderr.strip()[:500],
                })
            except subprocess.TimeoutExpired:
                results.append({
                    "scenario": sc_label, "cr": sc["cr"],
                    "status": "FAILED", "error": f"Timeout after {timeout}s",
                })
            except Exception as e:
                results.append({
                    "scenario": sc_label, "cr": sc["cr"],
                    "status": "FAILED", "error": str(e),
                })

        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = len(results) - passed
        overall = "passed" if failed == 0 and results else "failed"

        # Generate report HTML — includes links to actual MOP files
        html_path = str(run_dir / f"{uc_name}_mop-generation-report.html")
        self._generate_report_html(html_path, results, uc_name, all_mop_files, mop_dir)

        # If there's exactly one MOP file and it passed, use it as the artifact
        # Otherwise use the report
        if len(all_mop_files) == 1 and overall == "passed":
            artifact_html = str(all_mop_files[0])
        else:
            artifact_html = html_path

        return StepOutput(
            status=overall,
            error={"test_failures": [
                {"test": r["scenario"], "error": r.get("error", r.get("stderr", ""))}
                for r in results if r["status"] == "FAILED"
            ]} if failed else None,
            output_data={
                "artifacts": artifacts,
                "mop_results": results,
                "report_html": artifact_html,
                "mop_dir": str(mop_dir),
                "mop_files": [str(f) for f in all_mop_files],
            },
        )

    @staticmethod
    def _build_scenarios(json_data: dict) -> list[dict]:
        """Build MOP generation scenarios from JSON data."""
        scenarios = []
        cr_groups = json_data.get("crGroups", [])

        for cg in cr_groups:
            cr = cg.get("crGroup", "CR1")
            node_groups = cg.get("nodeGroups", [])

            if not node_groups:
                scenarios.append({"cr": cr, "label": f"CR={cr}"})
                continue

            # One scenario per CR (all nodes)
            scenarios.append({"cr": cr, "label": f"CR={cr} (all nodes)"})

            # One scenario per node within the CR
            for ng in node_groups:
                ng_name = ng.get("nodeGroup", "")
                nodes = ng.get("nodes", [])
                for node in nodes:
                    node_name = node.get("node", "")
                    if node_name:
                        scenarios.append({
                            "cr": cr,
                            "node": node_name,
                            "label": f"CR={cr} / {ng_name} / {node_name}",
                        })

        return scenarios

    def _generate_report_html(
        self, filepath: str, results: list[dict], uc_name: str,
        mop_files: list[Path], mop_dir: Path,
    ) -> None:
        """Generate HTML report for MOP generation milestone review."""
        import html as html_mod
        from nicegui import app

        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = len(results) - passed
        overall = "PASSED" if failed == 0 and results else "FAILED"
        overall_cls = "pass" if overall == "PASSED" else "fail"

        # Build scenario rows
        scenario_rows = ""
        for r in results:
            st = r["status"]
            cls = "pass" if st == "PASSED" else "fail"
            files = ", ".join(r.get("mop_files", []))
            error = html_mod.escape(r.get("error", r.get("stderr", "")))
            scenario_rows += f"""<tr class="{cls}-row">
              <td class="{cls}">{st}</td>
              <td>{html_mod.escape(r.get('scenario',''))}</td>
              <td>{html_mod.escape(r.get('cr',''))}</td>
              <td>{html_mod.escape(r.get('node','all'))}</td>
              <td>{html_mod.escape(files) if files else error}</td>
            </tr>"""

        # Build MOP file cards with View/Download links
        # Serve each MOP file via NiceGUI for browser access
        mop_cards = ""
        for f in mop_files:
            size_kb = f.stat().st_size / 1024
            try:
                serve_url = app.add_media_file(local_file=str(f))
            except Exception:
                serve_url = ""
            view_btn = (
                f'<a href="{serve_url}" target="_blank" class="btn btn-view">View</a>'
                if serve_url else ''
            )
            download_btn = (
                f'<a href="{serve_url}" download="{html_mod.escape(f.name)}" class="btn btn-download">Download</a>'
                if serve_url else ''
            )
            mop_cards += (
                f'<div class="file-card">'
                f'<span class="file-icon">&#128196;</span>'
                f'<span class="file-name">{html_mod.escape(f.name)}</span>'
                f'<span class="file-size">{size_kb:.1f} KB</span>'
                f'<span class="file-path">{html_mod.escape(str(f.parent.name))}/</span>'
                f'<span class="file-actions">{view_btn} {download_btn}</span>'
                f'</div>'
            )
        if not mop_cards:
            mop_cards = '<div class="file-card"><span class="file-name">No MOP files generated</span></div>'

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>MOP Generation Report — {html_mod.escape(uc_name)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #333; font-size: 13px; }}
.header {{ background: #1a3a5c; color: #fff; padding: 20px 30px; }}
.header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
.header .meta {{ font-size: 12px; color: #b0c4d8; }}
.summary {{ padding: 16px 24px; margin: 16px 24px; border-radius: 8px; border-left: 6px solid;
  display: flex; align-items: center; gap: 20px; }}
.summary.pass {{ background: #eafaf1; border-color: #27ae60; }}
.summary.fail {{ background: #fdf2f2; border-color: #e74c3c; }}
.summary .status {{ font-size: 28px; font-weight: 700; }}
.summary.pass .status {{ color: #27ae60; }}
.summary.fail .status {{ color: #e74c3c; }}
.stats {{ display: flex; gap: 20px; }}
.stat {{ text-align: center; }}
.stat-num {{ font-size: 20px; font-weight: 700; color: #1a3a5c; }}
.stat-num.green {{ color: #27ae60; }} .stat-num.red {{ color: #e74c3c; }}
.stat-label {{ font-size: 10px; text-transform: uppercase; color: #888; }}
.section {{ margin: 12px 24px; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }}
.section h2 {{ font-size: 14px; font-weight: 600; color: #1a3a5c; padding: 12px 16px;
  background: #f0f4f8; border-bottom: 1px solid #e0e4e8; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #1a3a5c; color: #fff; padding: 8px 12px; text-align: left; font-size: 11px; text-transform: uppercase; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #eef0f3; }}
.pass {{ color: #27ae60; font-weight: 700; }}
.fail {{ color: #e74c3c; font-weight: 700; }}
.pass-row td {{ background: #fafff8; }}
.fail-row td {{ background: #fef9f9; }}
.file-card {{ display: flex; align-items: center; gap: 10px; padding: 8px 16px;
  border-bottom: 1px solid #f0f0f0; }}
.file-icon {{ font-size: 20px; }}
.file-name {{ font-weight: 600; font-size: 13px; flex: 1; }}
.file-size {{ color: #888; font-size: 12px; }}
.file-path {{ color: #aaa; font-size: 11px; }}
.file-actions {{ display: flex; gap: 6px; }}
.btn {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 11px;
  font-weight: 600; text-decoration: none; text-transform: uppercase; letter-spacing: 0.5px; }}
.btn-view {{ background: #1a3a5c; color: #fff; }}
.btn-view:hover {{ background: #2c5282; }}
.btn-download {{ background: #27ae60; color: #fff; }}
.btn-download:hover {{ background: #219a52; }}
.footer {{ padding: 12px 30px; font-size: 11px; color: #999; text-align: center; margin-top: 12px; }}
</style></head>
<body>
<div class="header">
  <h1>MOP Generation Report</h1>
  <div class="meta">UC: {html_mod.escape(uc_name)}</div>
</div>
<div class="summary {overall_cls}">
  <div class="status">{overall}</div>
  <div class="stats">
    <div class="stat"><div class="stat-num">{len(results)}</div><div class="stat-label">Scenarios</div></div>
    <div class="stat"><div class="stat-num green">{passed}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-num red">{failed}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-num">{len(mop_files)}</div><div class="stat-label">MOP Files</div></div>
  </div>
</div>
<div class="section">
  <h2>Generation Scenarios</h2>
  <table>
    <thead><tr><th>Status</th><th>Scenario</th><th>CR Group</th><th>Node</th><th>Output / Error</th></tr></thead>
    <tbody>{scenario_rows}</tbody>
  </table>
</div>
<div class="section">
  <h2>Generated MOP Documents</h2>
  {mop_cards}
</div>
<div class="footer">Generated by CR Automation Pipeline — Step 7: MOP Generation Tester</div>
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
