"""Step 9: Report generation — HTML and Excel reports."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import xlsxwriter
from jinja2 import Environment, FileSystemLoader

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


class ReportGenerator:
    step_number = 9
    step_name = "Report Generator"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        run_dir = Path(input_data.get("run_dir", "."))
        run_data = input_data.get("run_data", {})
        step_results = input_data.get("step_results", [])
        regression_diff = input_data.get("regression_diff")

        html_path = run_dir / "report.html"
        excel_path = run_dir / "report.xlsx"

        # Generate HTML report
        html = self._generate_html(run_data, step_results, regression_diff)
        html_path.write_text(html, encoding="utf-8")

        # Generate Excel report
        self._generate_excel(str(excel_path), run_data, step_results)

        return StepOutput(
            status="passed",
            artifacts=[str(html_path), str(excel_path)],
            output_data={
                "html_report": str(html_path),
                "excel_report": str(excel_path),
            },
        )

    def _generate_html(
        self,
        run_data: dict,
        step_results: list[dict],
        regression_diff: Any = None,
    ) -> str:
        template_dir = Path("src/testing/report/templates")
        if template_dir.exists():
            env = Environment(loader=FileSystemLoader(str(template_dir)))
            try:
                tmpl = env.get_template("base.html")
                return tmpl.render(
                    run=run_data,
                    steps=step_results,
                    regression_diff=regression_diff,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass

        # Fallback: inline HTML
        steps_html = ""
        for s in step_results:
            color = "green" if s.get("status") == "passed" else "red"
            steps_html += (
                f"<tr>"
                f"<td>{s.get('step_number', '')}</td>"
                f"<td>{s.get('step_name', '')}</td>"
                f"<td style='color:{color}'>{s.get('status', '')}</td>"
                f"<td>{s.get('error_message', '')}</td>"
                f"</tr>"
            )

        regression_section = ""
        if regression_diff:
            regression_section = (
                f"<h2>Regression Diff</h2><pre>{str(regression_diff)}</pre>"
            )

        return (
            f"<!DOCTYPE html><html><head><title>Pipeline Report</title>\n"
            f"<style>"
            f"body{{font-family:sans-serif;background:#1a1a2e;color:#eee;padding:20px}} "
            f"table{{border-collapse:collapse;width:100%}} "
            f"th,td{{border:1px solid #444;padding:8px;text-align:left}} "
            f"th{{background:#16213e}}"
            f"</style></head>\n"
            f"<body>"
            f"<h1>Pipeline Test Report</h1>"
            f"<p>UC: {run_data.get('uc_name', '')}</p>"
            f"<p>Status: {run_data.get('status', '')}</p>\n"
            f"<p>Generated: {datetime.now(timezone.utc).isoformat()}</p>\n"
            f"<table>"
            f"<tr><th>Step</th><th>Name</th><th>Status</th><th>Error</th></tr>"
            f"{steps_html}"
            f"</table>\n"
            f"{regression_section}\n"
            f"</body></html>"
        )

    def _generate_excel(
        self,
        path: str,
        run_data: dict,
        step_results: list[dict],
    ) -> None:
        wb = xlsxwriter.Workbook(path)

        # Summary sheet
        ws = wb.add_worksheet("Summary")
        ws.write(0, 0, "UC Name")
        ws.write(0, 1, run_data.get("uc_name", ""))
        ws.write(1, 0, "Status")
        ws.write(1, 1, run_data.get("status", ""))
        ws.write(2, 0, "Generated")
        ws.write(2, 1, datetime.now(timezone.utc).isoformat())

        # Steps sheet
        ws2 = wb.add_worksheet("Steps")
        headers = ["Step", "Name", "Status", "Error"]
        for i, h in enumerate(headers):
            ws2.write(0, i, h)
        for row, s in enumerate(step_results, 1):
            ws2.write(row, 0, s.get("step_number", ""))
            ws2.write(row, 1, s.get("step_name", ""))
            ws2.write(row, 2, s.get("status", ""))
            ws2.write(row, 3, s.get("error_message", ""))

        wb.close()
