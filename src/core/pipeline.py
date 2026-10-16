"""Pipeline execution engine — orchestrates 9-step agent pipeline per FR-003, FR-034."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.core.events import PipelineEvent, event_bus
from src.core.hil import hil_handler
from src.core.models import (
    GeneratedConfig,
    PipelineConfiguration,
    PipelineRun,
    PipelineStatus,
    StepResult,
    StepStatus,
    UCDocumentSet,
)
from src.core.retry import RetryManager
from src.storage.artifact_store import ArtifactStore
from src.storage.db import Database

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Central controller that manages pipeline execution per contracts/pipeline-api.md."""

    def __init__(self, db: Database, artifact_store: ArtifactStore,
                 config: PipelineConfiguration | dict | None = None,
                 config_manager: Any | None = None) -> None:
        self.db = db
        self.artifact_store = artifact_store
        self.config = config or {}
        self.config_manager = config_manager  # live config source
        self._active_tasks: dict[UUID, asyncio.Task] = {}
        self._milestone_events: dict[UUID, asyncio.Event] = {}
        self._milestone_decisions: dict[UUID, str] = {}  # "continue", "rerun", "cancel"

    # Milestone steps — pause after each for user review
    MILESTONE_STEPS = {3, 4, 5, 6, 7, 8, 9}
    MILESTONE_NAMES = {
        3: "Validation Rules",
        4: "JSON Output Template",
        5: "CIQ Validation Test Results",
        6: "Workflow",
        7: "MOP Generation Test",
        8: "Simulator Config",
        9: "Execution Test Results",
    }

    async def start_run(self, document_set: UCDocumentSet,
                        review_milestones: bool = True) -> PipelineRun:
        """Start a new pipeline run for the given document set."""
        config_snapshot = self.config.model_dump() if hasattr(self.config, "model_dump") else dict(self.config)

        run = PipelineRun(
            document_set_id=document_set.id,
            config_snapshot=config_snapshot,
        )

        # Persist document set and run
        await self.db.insert_model("uc_document_sets", document_set.model_dump())
        await self.db.insert_model("pipeline_runs", run.model_dump())

        # Launch pipeline as background task
        task = asyncio.create_task(
            self._execute_pipeline(run, document_set, review_milestones=review_milestones)
        )
        self._active_tasks[run.id] = task
        return run

    async def cancel_run(self, run_id: UUID) -> None:
        """Cancel a running pipeline. Completed step artifacts are preserved."""
        # Mark as cancelled in DB immediately
        await self.db.update("pipeline_runs", str(run_id), {
            "status": PipelineStatus.CANCELLED.value,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        # Release any milestone wait
        evt = self._milestone_events.get(run_id)
        if evt:
            self._milestone_decisions[run_id] = "cancel"
            evt.set()

        # Cancel the background task (best effort)
        task = self._active_tasks.pop(run_id, None)
        if task and not task.done():
            task.cancel()

    async def get_run_status(self, run_id: UUID) -> dict | None:
        return await self.db.get_by_id("pipeline_runs", str(run_id))

    async def respond_to_milestone(self, run_id: UUID, decision: str) -> None:
        """User responds to a milestone pause: 'continue', 'rerun', or 'cancel'."""
        self._milestone_decisions[run_id] = decision
        evt = self._milestone_events.get(run_id)
        if evt:
            evt.set()

    async def respond_to_hil(self, hil_id: UUID, response: str) -> None:
        await hil_handler.respond(hil_id, response)
        await self.db.update("hil_requests", str(hil_id), {
            "user_response": response,
            "status": "answered",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        })

    async def resume_run(self, run_id: UUID, from_step: int = 1) -> PipelineRun:
        """Resume an orphaned pipeline run from a specific step.

        Re-uses the existing run and document set, launching the pipeline
        from the given step instead of creating a new run.
        """
        old_run = await self.db.get_by_id("pipeline_runs", str(run_id))
        if not old_run:
            raise ValueError(f"Run {run_id} not found")

        doc_set_id = old_run.get("document_set_id")
        doc_row = await self.db.get_by_id("uc_document_sets", str(doc_set_id))
        if not doc_row:
            raise ValueError(f"Document set {doc_set_id} not found")

        doc_set = UCDocumentSet(
            id=UUID(str(doc_set_id)),
            uc_name=doc_row["uc_name"],
            ciq_file_path=doc_row["ciq_file_path"],
            mop_file_path=doc_row["mop_file_path"],
            sample_io_xml_paths=json.loads(doc_row["sample_io_xml_paths"]) if doc_row.get("sample_io_xml_paths") else [],
            constraints_file_path=doc_row.get("constraints_file_path"),
            command_outputs_path=doc_row["command_outputs_path"],
            additional_documents=json.loads(doc_row["additional_documents"]) if doc_row.get("additional_documents") else None,
            validated=True,
        )

        run = PipelineRun(
            id=run_id,
            document_set_id=doc_set.id,
            config_snapshot=json.loads(old_run["config_snapshot"]) if isinstance(old_run.get("config_snapshot"), str) else old_run.get("config_snapshot", {}),
        )

        # Update run status back to running
        await self.db.update("pipeline_runs", str(run_id), {
            "status": PipelineStatus.RUNNING.value,
        })

        # Clean step results from from_step onward
        await self.db.execute_insert(
            "DELETE FROM step_results WHERE run_id = ? AND step_number >= ?",
            (str(run_id), from_step),
        )

        # Launch pipeline from the specified step
        task = asyncio.create_task(
            self._execute_pipeline(run, doc_set, review_milestones=True, start_from_step=from_step)
        )
        self._active_tasks[run.id] = task
        return run

    async def rerun(self, run_id: UUID) -> PipelineRun:
        """Re-run a previous pipeline using its original document set."""
        # Get the old run to find its document_set_id
        old_run = await self.db.get_by_id("pipeline_runs", str(run_id))
        if not old_run:
            raise ValueError(f"Run {run_id} not found")

        doc_set_id = old_run.get("document_set_id")
        doc_row = await self.db.get_by_id("uc_document_sets", str(doc_set_id))
        if not doc_row:
            raise ValueError(f"Document set {doc_set_id} not found")

        # Create a new UCDocumentSet with a fresh ID but same files
        new_doc_set = UCDocumentSet(
            uc_name=doc_row["uc_name"],
            ciq_file_path=doc_row["ciq_file_path"],
            mop_file_path=doc_row["mop_file_path"],
            sample_io_xml_paths=json.loads(doc_row["sample_io_xml_paths"]) if doc_row.get("sample_io_xml_paths") else [],
            constraints_file_path=doc_row.get("constraints_file_path"),
            command_outputs_path=doc_row["command_outputs_path"],
            additional_documents=json.loads(doc_row["additional_documents"]) if doc_row.get("additional_documents") else None,
            validated=True,
        )
        return await self.start_run(new_doc_set)

    async def list_runs(self, filters: dict | None = None) -> list[dict]:
        clauses = []
        vals: list = []
        if filters:
            if filters.get("uc_name"):
                clauses.append("d.uc_name LIKE ?")
                vals.append(f"%{filters['uc_name']}%")
            if filters.get("status"):
                clauses.append("r.status = ?")
                vals.append(filters["status"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT r.id, d.uc_name, r.status, r.started_at, r.completed_at, "
            "r.duration_seconds, r.current_step, r.error_summary "
            "FROM pipeline_runs r "
            "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id"
            f"{where} ORDER BY r.started_at DESC"
        )
        return await self.db.execute(sql, tuple(vals))

    async def _execute_pipeline(self, run: PipelineRun, doc_set: UCDocumentSet,
                                review_milestones: bool = True,
                                start_from_step: int = 1) -> None:
        """Execute pipeline steps sequentially, optionally resuming from a given step."""
        try:
            # Refresh config from ConfigManager to pick up settings changes made via UI
            if self.config_manager is not None:
                try:
                    self.config = await self.config_manager.get_config()
                except Exception:
                    pass  # fall back to existing config

            run.status = PipelineStatus.RUNNING
            if start_from_step <= 1:
                run.started_at = datetime.now(timezone.utc)
            else:
                run.started_at = run.started_at or datetime.now(timezone.utc)
            await self.db.update("pipeline_runs", str(run.id), {
                "status": run.status.value,
                "started_at": run.started_at.isoformat() if run.started_at else datetime.now(timezone.utc).isoformat(),
            })

            # Set API key in environment for liteLLM
            import os
            api_key = getattr(self.config, "openrouter_api_key", "") if hasattr(self.config, "openrouter_api_key") else ""
            if not api_key:
                # Try loading from DB settings
                try:
                    rows = await self.db.query("settings", where="key = ?", params=("api_key",))
                    if rows:
                        api_key = rows[0].get("value", "")
                except Exception:
                    pass
            if api_key:
                os.environ["OPENROUTER_API_KEY"] = api_key

            # Initialize agents
            agents = self._create_agents()
            retry_mgr = RetryManager(
                test_retry_limit=self.config.test_retry_limit if hasattr(self.config, "test_retry_limit") else 3,
                infra_retry_limit=self.config.infra_retry_limit if hasattr(self.config, "infra_retry_limit") else 3,
                infra_backoff=self.config.infra_retry_backoff if hasattr(self.config, "infra_retry_backoff") else 2.0,
            )

            # Load few-shot examples
            few_shot_examples = await self._load_few_shot(doc_set.uc_name)

            run_dir = self.artifact_store.run_dir(run.id)
            step_data: dict[str, Any] = {
                "document_set": doc_set.model_dump(),
                "run_dir": str(run_dir),
                "uc_name": doc_set.uc_name,
                "ciq_file_path": doc_set.ciq_file_path,
            }

            # When resuming from a later step, rebuild step_data from saved artifacts
            if start_from_step > 1:
                step_data = await self._rebuild_step_data(
                    run.id, doc_set, run_dir, step_data, start_from_step,
                )

            step_num = start_from_step - 1  # will increment at top of loop
            while step_num < 10:
                step_num += 1

                # Check if cancelled externally
                run_row = await self.db.get_by_id("pipeline_runs", str(run.id))
                if run_row and run_row.get("status") in (
                    PipelineStatus.CANCELLED.value, "cancelled",
                ):
                    logger.info("Pipeline %s cancelled externally, stopping", run.id)
                    return

                agent = agents[step_num]
                run.current_step = step_num
                await self.db.update("pipeline_runs", str(run.id), {"current_step": step_num})

                step_result = StepResult(
                    run_id=run.id, step_number=step_num,
                    step_name=agent.step_name, status=StepStatus.IN_PROGRESS,
                    started_at=datetime.now(timezone.utc),
                )
                await self.db.insert_model("step_results", step_result.model_dump())

                await event_bus.publish(PipelineEvent(
                    run_id=run.id, event_type="step_started",
                    step_number=step_num,
                    payload={"step_name": agent.step_name},
                ))

                # Execute with retry (skip retries for deterministic steps 7-9)
                async def _exec(**kwargs):
                    return (await agent.execute(
                        input_data=step_data,
                        config=self.config,
                        few_shot_examples=few_shot_examples if step_num in (1, 2, 3, 4, 6, 8) else None,
                        **kwargs,
                    )).model_dump()

                if step_num in (5, 7, 9, 10):
                    # Deterministic steps — run once, no retries
                    try:
                        raw = await _exec()
                        step_status = raw.get("status", "passed")
                        # Steps 5 & 8: always flow to milestone review (pass or fail)
                        # Step 9: always succeeds (final report)
                        result = {"success": True, "result": raw, "attempts": [], "needs_cascade": False}
                    except Exception as exc:
                        result = {"success": False, "result": {}, "attempts": [],
                                  "error": str(exc), "needs_cascade": False}
                else:
                    result = await retry_mgr.execute_with_retry(
                        step_num, _exec,
                        on_retry=lambda s, n, e: event_bus.publish(PipelineEvent(
                            run_id=run.id, event_type="retry_started",
                            step_number=s,
                            payload={"attempt": n, "error": str(e)},
                        )),
                    )

                if result["success"]:
                    output = result["result"]
                    step_result.status = StepStatus.PASSED

                    # Handle HIL requests from steps 1-2
                    hil_requests = output.get("hil_requests")
                    if hil_requests and step_num <= 2:
                        run.status = PipelineStatus.PAUSED_HIL
                        await self.db.update("pipeline_runs", str(run.id), {
                            "status": PipelineStatus.PAUSED_HIL.value,
                        })

                        responses = await hil_handler.batch_request_and_wait(
                            run.id, step_num,
                            hil_requests if isinstance(hil_requests, list) else [],
                        )

                        run.status = PipelineStatus.RUNNING
                        await self.db.update("pipeline_runs", str(run.id), {
                            "status": PipelineStatus.RUNNING.value,
                        })

                    # Update step data for next step
                    step_output = output.get("output_data", {})
                    if step_num <= 2:
                        # Steps 1-2: Build intermediate representation
                        step_data["intermediate_repr"] = step_output
                        run.intermediate_repr = step_output
                        # Flatten IR fields into step_data for generation agents
                        if isinstance(step_output, dict):
                            for ir_key in ("uc_metadata", "ciq_data", "mop_data",
                                           "constraints", "command_outputs",
                                           "simulator_config", "field_types"):
                                if ir_key in step_output:
                                    step_data[ir_key] = step_output[ir_key]
                    elif step_num in (3, 4, 6, 8):
                        # LLM generation steps: accumulate YAML artifacts
                        artifacts = step_data.get("artifacts", {})
                        artifacts.update(step_output)
                        step_data["artifacts"] = artifacts
                    elif step_num == 5:
                        # CIQ validation test results — save to disk for resume
                        step_data["ciq_test_results"] = step_output
                        try:
                            qa_save = run_dir / "qa_feedback.json"
                            failed = [tc for tc in step_output.get("test_cases", [])
                                      if tc.get("status") == "FAIL"]
                            if failed:
                                qa_save.write_text(json.dumps(failed, default=str), encoding="utf-8")
                        except Exception:
                            pass
                    elif step_num == 7:
                        # MOP generation test results
                        step_data["mop_test_results"] = step_output
                    elif step_num == 9:
                        # Execution test results
                        step_data["exec_test_results"] = step_output
                    elif step_num == 10:
                        # Final report paths
                        step_data["report_paths"] = step_output

                    # Save artifacts
                    for artifact_path in output.get("artifacts", []):
                        if Path(artifact_path).exists():
                            cfg_type = self._infer_config_type(artifact_path)
                            if cfg_type is None:
                                continue  # skip unrecognized file types
                            content_hash = self.artifact_store.content_hash(artifact_path)
                            await self.db.insert_model("generated_configs", GeneratedConfig(
                                run_id=run.id,
                                config_type=cfg_type,
                                file_path=artifact_path,
                                schema_valid=step_num >= 8,
                                generated_by_step=step_num,
                                content_hash=content_hash,
                            ).model_dump())

                    # --- Milestone review pause ---
                    if review_milestones and step_num in self.MILESTONE_STEPS:
                        # Write artifact to disk so user can review
                        uc_name = doc_set.uc_name
                        artifact_key_map = {
                            3: ("validation_rules_yaml", f"{uc_name}_validation-rules.yaml"),
                            4: ("json_template_yaml", f"{uc_name}_json-output.yaml"),
                            6: ("workflow_yaml", f"{uc_name}.yaml"),
                        }

                        if step_num == 5:
                            # Step 5: CIQ QA HTML report
                            ak = "ciq_test_results"
                            html_report = step_output.get("report_html", "")
                            if html_report and Path(html_report).exists():
                                artifact_file = html_report
                            else:
                                fname = f"{uc_name}_ciq-test-results.txt"
                                artifact_file = str(run_dir / fname)
                                self._write_test_results_summary(
                                    artifact_file, step_output, uc_name, mode="ciq",
                                )
                        elif step_num == 7:
                            # Step 7: MOP generation test — show report or first MOP HTML
                            ak = "mop_test_results"
                            html_report = step_output.get("report_html", "")
                            if html_report and Path(html_report).exists():
                                artifact_file = html_report
                            else:
                                # Fallback: check mop_output for any HTML
                                mop_dir = run_dir / "mop_output"
                                mop_htmls = list(mop_dir.rglob("*.html")) if mop_dir.exists() else []
                                if mop_htmls:
                                    artifact_file = str(mop_htmls[0])
                                else:
                                    # Generate a simple error page
                                    err_path = run_dir / f"{uc_name}_mop-generation-report.html"
                                    err_msg = str(output.get("error", {}).get("message", "MOP generation produced no output"))
                                    err_path.write_text(
                                        f"<html><body style='font-family:sans-serif;padding:20px'>"
                                        f"<h2 style='color:#e74c3c'>MOP Generation Failed</h2>"
                                        f"<pre>{err_msg}</pre></body></html>",
                                        encoding="utf-8",
                                    )
                                    artifact_file = str(err_path)
                        elif step_num == 8:
                            # Step 8: Simulator config HTML overview
                            ak = "simulator_config_yaml"
                            sim_dir = step_output.get("simulator_dir", "")
                            html_path = str(run_dir / f"{uc_name}_simulator-overview.html")
                            self._write_simulator_overview_html(
                                html_path, step_output, uc_name, sim_dir,
                            )
                            artifact_file = html_path
                        elif step_num == 9:
                            # Step 9: Execution test results — prefer JAR-generated reports
                            ak = "exec_test_results"
                            summary_report = step_output.get("summary_html", "")
                            detail_report = step_output.get("report_html", "")
                            if summary_report and Path(summary_report).exists():
                                artifact_file = summary_report
                            elif detail_report and Path(detail_report).exists():
                                artifact_file = detail_report
                            else:
                                # Fallback: generate built-in report
                                html_path = str(run_dir / f"{uc_name}_exec-test-report.html")
                                self._write_exec_test_report_html(
                                    html_path, step_output, uc_name,
                                )
                                artifact_file = html_path
                        elif step_num == 99999:
                            # Legacy text format (unused)
                            ak = "exec_test_results"
                            fname = f"{uc_name}_exec-test-results.txt"
                            artifact_file = str(run_dir / fname)
                            self._write_test_results_summary(
                                artifact_file, step_output, uc_name, mode="exec",
                            )
                        else:
                            ak, fname = artifact_key_map.get(step_num, ("", ""))
                            content = step_data.get("artifacts", {}).get(ak, "")
                            artifact_file = None
                            if content and fname:
                                artifact_file = str(run_dir / fname)
                                Path(artifact_file).write_text(content, encoding="utf-8")

                        run.status = PipelineStatus.PAUSED_MILESTONE
                        await self.db.update("pipeline_runs", str(run.id), {
                            "status": PipelineStatus.PAUSED_MILESTONE.value,
                        })

                        milestone_name = self.MILESTONE_NAMES.get(step_num, f"Step {step_num}")
                        milestone_payload = {
                            "milestone": milestone_name,
                            "artifact_file": artifact_file,
                            "artifact_key": ak,
                        }

                        # For step 9, include extra report files for download
                        if step_num == 9:
                            extra_files = []
                            for rkey in ("summary_html", "report_html"):
                                rpath = step_output.get(rkey, "")
                                if rpath and Path(rpath).exists() and rpath != artifact_file:
                                    extra_files.append(rpath)
                            if extra_files:
                                milestone_payload["extra_artifact_files"] = extra_files

                        # For test steps 5, 7, 9, include test pass/fail status
                        if step_num in (5, 7, 9):
                            test_status = output.get("status", "passed")
                            milestone_payload["test_status"] = test_status
                            if test_status == "failed":
                                failed_labels = {
                                    5: "CIQ Test Results — FAILED",
                                    7: "MOP Generation Test — FAILED",
                                    9: "Execution Test Results — FAILED",
                                }
                                milestone_payload["milestone"] = failed_labels.get(step_num, milestone_name)

                        # Create event BEFORE publishing so _restore_active_run
                        # can find it (avoids race with the 0.5s restore timer)
                        evt = asyncio.Event()
                        self._milestone_events[run.id] = evt
                        self._milestone_decisions.pop(run.id, None)

                        await event_bus.publish(PipelineEvent(
                            run_id=run.id, event_type="milestone_reached",
                            step_number=step_num,
                            payload=milestone_payload,
                        ))

                        # Wait for user decision
                        await evt.wait()
                        decision = self._milestone_decisions.pop(run.id, "continue")
                        self._milestone_events.pop(run.id, None)

                        if decision == "cancel":
                            run.status = PipelineStatus.CANCELLED
                            run.completed_at = datetime.now(timezone.utc)
                            await self.db.update("pipeline_runs", str(run.id), {
                                "status": PipelineStatus.CANCELLED.value,
                                "completed_at": run.completed_at.isoformat(),
                            })
                            await event_bus.publish(PipelineEvent(
                                run_id=run.id, event_type="run_cancelled",
                            ))
                            return
                        elif decision == "rerun":
                            run.status = PipelineStatus.RUNNING
                            await self.db.update("pipeline_runs", str(run.id), {
                                "status": PipelineStatus.RUNNING.value,
                            })
                            # Delete the step result so it can be re-created
                            await self.db.execute_insert(
                                "DELETE FROM step_results WHERE id = ?",
                                (str(step_result.id),),
                            )

                            if step_num == 5:
                                # Step 5 rerun: go back to step 3 to re-generate
                                # validation rules + JSON output, then re-test.
                                # Preserve QA results as feedback for Step 3 LLM.
                                qa_results = step_data.get("ciq_test_results", {})
                                failed_tcs = [
                                    tc for tc in qa_results.get("test_cases", [])
                                    if tc.get("status") == "FAIL"
                                ]
                                if failed_tcs:
                                    step_data["qa_feedback"] = failed_tcs
                                step_data.get("artifacts", {}).pop("validation_rules_yaml", None)
                                step_data.get("artifacts", {}).pop("json_template_yaml", None)
                                step_data.pop("ciq_test_results", None)
                                # Delete step 3, 4, 5 results for clean re-run
                                await self.db.execute_insert(
                                    "DELETE FROM step_results WHERE run_id = ? AND step_number IN (3, 4, 5)",
                                    (str(run.id),),
                                )
                                await event_bus.publish(PipelineEvent(
                                    run_id=run.id, event_type="retry_started",
                                    step_number=3,
                                    payload={"attempt": 1, "error": "User requested re-run from Step 5 review"},
                                ))
                                step_num = 2  # will increment to 3 at loop top
                            elif step_num == 7:
                                # Step 7 (MOP gen) rerun: go back to step 6 to re-generate workflow
                                step_data.get("artifacts", {}).pop("workflow_yaml", None)
                                step_data.pop("mop_test_results", None)
                                await self.db.execute_insert(
                                    "DELETE FROM step_results WHERE run_id = ? AND step_number IN (6, 7)",
                                    (str(run.id),),
                                )
                                await event_bus.publish(PipelineEvent(
                                    run_id=run.id, event_type="retry_started",
                                    step_number=6,
                                    payload={"attempt": 1, "error": "User requested re-run from Step 7 review"},
                                ))
                                step_num = 5  # will increment to 6
                            elif step_num == 9:
                                # Step 9 (exec test) rerun: go back to step 6 to re-generate
                                # workflow + simulator config, then re-test
                                step_data.get("artifacts", {}).pop("workflow_yaml", None)
                                step_data.get("artifacts", {}).pop("simulator_config_yaml", None)
                                step_data.pop("mop_test_results", None)
                                step_data.pop("exec_test_results", None)
                                await self.db.execute_insert(
                                    "DELETE FROM step_results WHERE run_id = ? AND step_number IN (6, 7, 8, 9)",
                                    (str(run.id),),
                                )
                                await event_bus.publish(PipelineEvent(
                                    run_id=run.id, event_type="retry_started",
                                    step_number=6,
                                    payload={"attempt": 1, "error": "User requested re-run from Step 9 review"},
                                ))
                                step_num = 5  # will increment to 6
                            else:
                                # LLM steps: re-run this step
                                step_data.get("artifacts", {}).pop(ak, None)
                                await event_bus.publish(PipelineEvent(
                                    run_id=run.id, event_type="retry_started",
                                    step_number=step_num,
                                    payload={"attempt": 1, "error": "User requested re-run"},
                                ))
                                step_num -= 1  # decrement so while loop re-runs same step
                            continue
                        else:
                            # Continue
                            run.status = PipelineStatus.RUNNING
                            await self.db.update("pipeline_runs", str(run.id), {
                                "status": PipelineStatus.RUNNING.value,
                            })

                else:
                    step_result.status = StepStatus.FAILED
                    step_result.error_message = result.get("error", "Unknown error")

                    await self._fail_run(run, step_result)
                    return

                step_result.completed_at = datetime.now(timezone.utc)
                await self.db.update("step_results", str(step_result.id), {
                    "status": step_result.status.value,
                    "completed_at": step_result.completed_at.isoformat(),
                    "retry_count": len(result.get("attempts", [])),
                })

                await event_bus.publish(PipelineEvent(
                    run_id=run.id, event_type="step_completed",
                    step_number=step_num,
                    payload={"status": step_result.status.value},
                ))

            # Pipeline completed successfully
            run.status = PipelineStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            run.duration_seconds = (run.completed_at - run.started_at).total_seconds()

            await self.db.update("pipeline_runs", str(run.id), {
                "status": PipelineStatus.COMPLETED.value,
                "completed_at": run.completed_at.isoformat(),
                "duration_seconds": run.duration_seconds,
                "intermediate_repr": json.dumps(run.intermediate_repr) if run.intermediate_repr else None,
            })

            # Add to knowledge base on success
            await self._add_to_knowledge_base(run, doc_set)

            await event_bus.publish(PipelineEvent(
                run_id=run.id, event_type="run_completed",
                payload={"duration": run.duration_seconds},
            ))

        except asyncio.CancelledError:
            logger.info("Pipeline %s cancelled", run.id)
            try:
                run.status = PipelineStatus.CANCELLED
                run.completed_at = datetime.now(timezone.utc)
                await self.db.update("pipeline_runs", str(run.id), {
                    "status": PipelineStatus.CANCELLED.value,
                    "completed_at": run.completed_at.isoformat(),
                })
            except Exception:
                pass
        except Exception as exc:
            logger.exception("Pipeline %s failed: %s", run.id, exc)
            await self.db.update("pipeline_runs", str(run.id), {
                "status": PipelineStatus.FAILED.value,
                "error_summary": str(exc),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            await event_bus.publish(PipelineEvent(
                run_id=run.id, event_type="run_failed",
                payload={"error": str(exc)},
            ))
        finally:
            self._active_tasks.pop(run.id, None)

    async def _rebuild_step_data(
        self, run_id: UUID, doc_set: UCDocumentSet,
        run_dir: Path, step_data: dict, start_from_step: int,
    ) -> dict:
        """Rebuild step_data from saved artifacts when resuming a run mid-pipeline."""
        uc_name = doc_set.uc_name

        # Reload intermediate repr from DB
        run_row = await self.db.get_by_id("pipeline_runs", str(run_id))
        if run_row and run_row.get("intermediate_repr"):
            try:
                ir = json.loads(run_row["intermediate_repr"]) if isinstance(run_row["intermediate_repr"], str) else run_row["intermediate_repr"]
                step_data["intermediate_repr"] = ir
                if isinstance(ir, dict):
                    for ir_key in ("uc_metadata", "ciq_data", "mop_data",
                                   "constraints", "command_outputs",
                                   "simulator_config", "field_types"):
                        if ir_key in ir:
                            step_data[ir_key] = ir[ir_key]
            except Exception:
                pass

        # Reload YAML artifacts from disk
        artifacts = {}
        artifact_files = {
            "validation_rules_yaml": f"{uc_name}_validation-rules.yaml",
            "json_template_yaml": f"{uc_name}_json-output.yaml",
            "workflow_yaml": f"{uc_name}.yaml",
            "simulator_config_yaml": f"{uc_name}_simulator-config.yaml",
        }
        for key, fname in artifact_files.items():
            fpath = run_dir / fname
            if fpath.exists():
                artifacts[key] = fpath.read_text(encoding="utf-8")

        # Clear artifacts that will be regenerated
        if start_from_step <= 3:
            artifacts.pop("validation_rules_yaml", None)
            artifacts.pop("json_template_yaml", None)
        if start_from_step <= 6:
            artifacts.pop("workflow_yaml", None)
            artifacts.pop("simulator_config_yaml", None)

        step_data["artifacts"] = artifacts

        # Load QA feedback from previous Step 5 run (for Step 3 re-generation)
        qa_feedback_file = run_dir / "qa_feedback.json"
        if qa_feedback_file.exists() and start_from_step <= 5:
            try:
                step_data["qa_feedback"] = json.loads(
                    qa_feedback_file.read_text(encoding="utf-8"))
                logger.info("Loaded QA feedback from %s", qa_feedback_file)
            except Exception:
                pass

        logger.info("Rebuilt step_data for resume from step %d: artifacts=%s",
                     start_from_step, list(artifacts.keys()))
        return step_data

    def _create_agents(self) -> dict:
        from src.agents.doc_parser_agent import DocParserAgent
        from src.agents.uc_analyzer_agent import UCAnalyzerAgent
        from src.agents.validation_gen_agent import ValidationGenAgent
        from src.agents.json_template_agent import JsonTemplateAgent
        from src.agents.workflow_gen_agent import WorkflowGenAgent
        from src.agents.mop_gen_tester import MopGenTester
        from src.agents.simulator_setup import SimulatorSetupAgent
        from src.agents.test_executor import TestExecutor
        from src.agents.report_generator import ReportGenerator

        return {
            1: DocParserAgent(),           # LLM: Parse documents → IR
            2: UCAnalyzerAgent(),          # LLM: Enrich IR
            3: ValidationGenAgent(),       # LLM: validation-rules.yaml
            4: JsonTemplateAgent(),        # LLM: json-output.yaml
            5: TestExecutor(mode="ciq"),   # QA: ciq-processor JAR test cycle
            6: WorkflowGenAgent(),         # LLM: workflow.yaml
            7: MopGenTester(),             # Deterministic: MOP generation test
            8: SimulatorSetupAgent(),      # LLM: MockWire simulator config + setup
            9: TestExecutor(mode="exec"),  # QA: cli-automation JAR test cycle
            10: ReportGenerator(),         # Deterministic: combined report
        }

    async def _load_few_shot(self, uc_name: str) -> list[dict]:
        try:
            from src.knowledge.similarity import SimilarityMatcher
            from src.knowledge.few_shot_store import FewShotStore
            store = FewShotStore()
            matcher = SimilarityMatcher(store)
            return await matcher.get_few_shot_context(uc_name, top_k=3)
        except Exception:
            return []

    async def _add_to_knowledge_base(self, run: PipelineRun, doc_set: UCDocumentSet) -> None:
        try:
            from src.knowledge.few_shot_store import FewShotStore
            store = FewShotStore()
            configs = await self.db.query("generated_configs",
                                           where="run_id = ?", params=(str(run.id),))
            config_map = {c["config_type"]: c["file_path"] for c in configs}
            await store.add_entry(
                uc_name=doc_set.uc_name,
                uc_description=doc_set.uc_name,
                configs=config_map,
                source_run_id=run.id,
            )
        except Exception as exc:
            logger.warning("Failed to add to knowledge base: %s", exc)

    async def _fail_run(self, run: PipelineRun, step_result: StepResult) -> None:
        run.status = PipelineStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        run.error_summary = f"Step {step_result.step_number} ({step_result.step_name}) failed: {step_result.error_message}"

        await self.db.update("pipeline_runs", str(run.id), {
            "status": PipelineStatus.FAILED.value,
            "error_summary": run.error_summary,
            "completed_at": run.completed_at.isoformat(),
        })

        await event_bus.publish(PipelineEvent(
            run_id=run.id, event_type="run_failed",
            step_number=step_result.step_number,
            payload={"error": step_result.error_message},
        ))

    @staticmethod
    @staticmethod
    @staticmethod
    def _write_exec_test_report_html(filepath: str, step_output: dict, uc_name: str) -> None:
        """Generate HTML report for Step 9 execution test results."""
        import html as html_mod

        results = step_output.get("results", {}) if isinstance(step_output, dict) else {}
        et = results.get("execution_test", {})

        rc = et.get("returncode", "N/A") if et else "N/A"
        status = "PASSED" if rc == 0 else "FAILED" if et else "NOT RUN"
        status_cls = "pass" if status == "PASSED" else "fail"
        stdout = html_mod.escape(et.get("stdout", "").strip()) if et else ""
        stderr = html_mod.escape(et.get("stderr", "").strip()) if et else ""

        # Check for failures in test_failures
        failures = step_output.get("test_failures", []) if isinstance(step_output, dict) else []
        if not failures and isinstance(step_output, dict):
            # Check parent error
            pass

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Execution Test Report — {html_mod.escape(uc_name)}</title>
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
.section {{ margin: 12px 24px; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; padding: 16px; }}
.section h2 {{ font-size: 14px; font-weight: 600; color: #1a3a5c; margin-bottom: 12px; }}
pre {{ background: #f8f9fa; padding: 10px 14px; border-radius: 4px; font-size: 12px;
  overflow-x: auto; white-space: pre-wrap; word-break: break-word; }}
pre.stderr {{ background: #fef5f5; color: #c0392b; }}
.footer {{ padding: 12px 30px; font-size: 11px; color: #999; text-align: center; margin-top: 12px; }}
</style></head>
<body>
<div class="header">
  <h1>Execution Test Report</h1>
  <div class="meta">UC: {html_mod.escape(uc_name)} | Exit code: {rc}</div>
</div>
<div class="summary {status_cls}">
  <div class="status">{status}</div>
</div>
{'<div class="section"><h2>Standard Output</h2><pre>' + stdout + '</pre></div>' if stdout else ''}
{'<div class="section"><h2>Error Output</h2><pre class="stderr">' + stderr + '</pre></div>' if stderr else ''}
{'<div class="section"><h2>No Execution Results</h2><p>The execution test did not produce any output.</p></div>' if not et else ''}
<div class="footer">Generated by CR Automation Pipeline — Step 9: Execution Tester</div>
</body></html>"""

        Path(filepath).write_text(html, encoding="utf-8")

    @staticmethod
    def _write_simulator_overview_html(
        filepath: str, step_output: dict, uc_name: str, sim_dir: str,
    ) -> None:
        """Generate an HTML overview of the simulator setup for milestone review."""
        import html as html_mod

        node_configs = step_output.get("node_configs", {})
        sim_path = Path(sim_dir) if sim_dir else None

        # Read config files
        config_sections = ""
        for node_name, config_path in node_configs.items():
            cp = Path(config_path)
            content = cp.read_text(encoding="utf-8") if cp.exists() else "(not found)"
            config_sections += (
                f'<div class="section">'
                f'<div class="section-header">'
                f'<span class="badge">CONFIG</span> {html_mod.escape(cp.name)}'
                f'</div>'
                f'<pre>{html_mod.escape(content)}</pre></div>'
            )

        # List response data files
        data_rows = ""
        if sim_path:
            data_dir = sim_path / "data"
            if data_dir.exists():
                for f in sorted(data_dir.iterdir()):
                    if f.is_file():
                        content = f.read_text(encoding="utf-8")
                        preview = content[:300] + ("..." if len(content) > 300 else "")
                        data_rows += (
                            f'<div class="section">'
                            f'<div class="section-header">'
                            f'<span class="badge data">RESPONSE</span> {html_mod.escape(f.name)}'
                            f'</div>'
                            f'<pre>{html_mod.escape(preview)}</pre></div>'
                        )

        # Manifest
        manifest_path = sim_path / "simulator_manifest.yaml" if sim_path else None
        manifest_content = ""
        if manifest_path and manifest_path.exists():
            manifest_content = manifest_path.read_text(encoding="utf-8")

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Simulator Setup — {html_mod.escape(uc_name)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #333; font-size: 13px; }}
.header {{ background: #1a3a5c; color: #fff; padding: 20px 30px; }}
.header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
.header .meta {{ font-size: 12px; color: #b0c4d8; }}
.summary {{ padding: 16px 24px; margin: 16px 24px; background: #eafaf1; border-radius: 8px;
  border-left: 6px solid #27ae60; display: flex; gap: 24px; align-items: center; }}
.stat {{ text-align: center; }}
.stat-num {{ font-size: 20px; font-weight: 700; color: #1a3a5c; }}
.stat-label {{ font-size: 10px; text-transform: uppercase; color: #888; }}
.section {{ margin: 12px 24px; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }}
.section-header {{ padding: 10px 16px; font-weight: 600; font-size: 13px;
  background: #f0f4f8; border-bottom: 1px solid #e0e4e8; display: flex; align-items: center; gap: 10px; }}
.badge {{ background: #1a3a5c; color: #fff; font-size: 10px; padding: 2px 8px; border-radius: 4px;
  text-transform: uppercase; letter-spacing: 0.5px; }}
.badge.data {{ background: #27ae60; }}
pre {{ padding: 12px 16px; font-size: 12px; overflow-x: auto; white-space: pre-wrap;
  word-break: break-word; background: #fafbfc; margin: 0; }}
.footer {{ padding: 12px 30px; font-size: 11px; color: #999; text-align: center; margin-top: 12px; }}
</style></head>
<body>
<div class="header">
  <h1>Simulator Setup Overview</h1>
  <div class="meta">UC: {html_mod.escape(uc_name)}</div>
</div>
<div class="summary">
  <div class="stat"><div class="stat-num">{len(node_configs)}</div><div class="stat-label">Simulator Instances</div></div>
  <div class="stat"><div class="stat-num">{len(list((sim_path / 'data').iterdir())) if sim_path and (sim_path / 'data').exists() else 0}</div><div class="stat-label">Response Files</div></div>
</div>
{config_sections}
{data_rows}
{'<div class="section"><div class="section-header"><span class="badge">MANIFEST</span> simulator_manifest.yaml</div><pre>' + html_mod.escape(manifest_content) + '</pre></div>' if manifest_content else ''}
<div class="footer">Generated by CR Automation Pipeline — Step 7: Simulator Setup</div>
</body></html>"""

        Path(filepath).write_text(html, encoding="utf-8")

    @staticmethod
    def _write_test_results_summary(
        filepath: str, test_output: dict, uc_name: str, mode: str = "ciq",
    ) -> None:
        """Write a human-readable test results summary for milestone review.

        mode="ciq"  — Step 5: CIQ validation tests (validation_test, json_output_test)
        mode="exec" — Step 8: Execution tests (execution_test)
        """
        results = test_output.get("results", {}) if isinstance(test_output, dict) else {}

        if mode == "ciq":
            lines = [
                f"CIQ Validation Test Report — {uc_name}",
                "=" * 60,
                "",
            ]

            test_cases = test_output.get("test_cases", []) if isinstance(test_output, dict) else []
            if not test_cases:
                lines.append("[SKIPPED] No test cases were executed")
                lines.append("")
            else:
                for i, tc in enumerate(test_cases, 1):
                    name = tc.get("name", f"Test Case {i}")
                    status = tc.get("status", "UNKNOWN")
                    lines.append(f"[{status}] {name}")

                    # Show error if failed
                    error = tc.get("error", "")
                    if error:
                        lines.append(f"  Error: {error}")

                    # Show stdout summary (key=value lines from JAR)
                    stdout = tc.get("stdout", "")
                    if stdout and stdout.strip():
                        for line in stdout.strip().splitlines():
                            lines.append(f"  {line}")

                    # Show stderr if present
                    stderr = tc.get("stderr", "")
                    if stderr and stderr.strip():
                        lines.append(f"  stderr: {stderr.strip()}")

                    # Show parsed validation report details
                    report = tc.get("report", {})
                    if report:
                        lines.append(f"  Validation Status: {report.get('status', 'N/A')}")
                        lines.append(f"  Total Errors: {report.get('totalErrors', 0)}")
                        sheets = report.get("sheets", [])
                        if sheets:
                            lines.append(f"  Sheets Validated:")
                            for sh in sheets:
                                sh_status = sh.get("status", "?")
                                sh_name = sh.get("sheetName", "?")
                                sh_rows = sh.get("rowsChecked", 0)
                                sh_errs = len(sh.get("errors", []))
                                lines.append(f"    [{sh_status}] {sh_name}  ({sh_rows} rows, {sh_errs} errors)")
                                for err in sh.get("errors", [])[:5]:
                                    row = err.get("row", "?")
                                    col = err.get("column", "?")
                                    msg = err.get("message", str(err))
                                    lines.append(f"          Row {row}, {col}: {msg}")
                        global_errs = report.get("globalErrors", [])
                        if global_errs:
                            lines.append(f"  Global Errors:")
                            for ge in global_errs[:5]:
                                lines.append(f"    - {ge}")
                        params = report.get("parameters", {})
                        if params:
                            lines.append(f"  Output Parameters:")
                            for pk, pv in params.items():
                                lines.append(f"    {pk}: {pv}")

                    # Show JSON output structure details (TC3)
                    details = tc.get("details", {})
                    if details:
                        lines.append(f"  JSON Output Structure:")
                        lines.append(f"    Schema Version: {details.get('schemaVersion', 'N/A')}")
                        lines.append(f"    CR Groups: {details.get('crGroups', 0)}")
                        lines.append(f"    Total Nodes: {details.get('totalNodes', 0)}")
                        lines.append(f"    Config Data Rows: {details.get('totalConfigRows', 0)}")

                    lines.append("")

            rerun_tip = "Click 'Re-run Steps 3-5' to re-generate validation rules and JSON output, then re-test."

        else:  # mode == "exec"
            lines = [
                f"Execution Test Results — {uc_name}",
                "=" * 60,
                "",
            ]

            et = results.get("execution_test", {})
            if et:
                rc = et.get("returncode", "N/A")
                status = "PASSED" if rc == 0 else "FAILED"
                lines.append(f"[{status}] Workflow Execution Test  (exit code: {rc})")
                if et.get("stdout", "").strip():
                    lines.append(f"  stdout: {et['stdout'].strip()}")
                if et.get("stderr", "").strip():
                    lines.append(f"  stderr: {et['stderr'].strip()}")
                lines.append("")
            else:
                lines.append("[SKIPPED] Workflow Execution Test — not executed")
                lines.append("")

            rerun_tip = "Click 'Re-run Steps 6-8' to re-generate workflow and simulator config, then re-test."

        # Overall
        test_cases = test_output.get("test_cases", []) if isinstance(test_output, dict) else []
        all_results = list(results.values()) if results else []
        all_passed = (
            all(tc.get("status") == "PASSED" for tc in test_cases)
            if test_cases
            else all(r.get("returncode") == 0 for r in all_results if isinstance(r, dict) and "returncode" in r)
        )
        has_results = bool(test_cases) or any(
            isinstance(r, dict) and "returncode" in r for r in all_results
        )
        if has_results:
            lines.append("=" * 60)
            lines.append(f"Overall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
            if not all_passed:
                lines.append("")
                lines.append(f"Tip: {rerun_tip}")
        else:
            lines.append("=" * 60)
            lines.append("No tests were executed (JAR not found or no artifacts available)")

        Path(filepath).write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _infer_config_type(path: str) -> str | None:
        """Infer config type from filename. Returns None for unrecognized files."""
        p = Path(path)
        name = p.stem.lower()
        suffix = p.suffix.lower()
        if "validation" in name or "rules" in name:
            return "validation_rules"
        if "json-output" in name or "json_template" in name or "template" in name:
            return "json_template"
        if "simulator" in name:
            return "simulator_config"
        if suffix == ".py":
            return "python_script"
        # workflow is the base UC name file (NODE_ACTIVITY.yaml) — match last
        if suffix == ".yaml" and "report" not in name and "config" not in name:
            return "workflow"
        return None
