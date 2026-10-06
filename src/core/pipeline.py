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
                 config: PipelineConfiguration | dict | None = None) -> None:
        self.db = db
        self.artifact_store = artifact_store
        self.config = config or {}
        self._active_tasks: dict[UUID, asyncio.Task] = {}

    async def start_run(self, document_set: UCDocumentSet) -> PipelineRun:
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
        task = asyncio.create_task(self._execute_pipeline(run, document_set))
        self._active_tasks[run.id] = task
        return run

    async def cancel_run(self, run_id: UUID) -> PipelineRun | None:
        """Cancel a running pipeline. Completed step artifacts are preserved."""
        task = self._active_tasks.get(run_id)
        if task and not task.done():
            task.cancel()

        await self.db.update("pipeline_runs", str(run_id), {
            "status": PipelineStatus.CANCELLED.value,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        await event_bus.publish(PipelineEvent(
            run_id=run_id, event_type="run_cancelled",
        ))

        rows = await self.db.get_by_id("pipeline_runs", str(run_id))
        return PipelineRun(**rows) if rows else None

    async def get_run_status(self, run_id: UUID) -> dict | None:
        return await self.db.get_by_id("pipeline_runs", str(run_id))

    async def respond_to_hil(self, hil_id: UUID, response: str) -> None:
        await hil_handler.respond(hil_id, response)
        await self.db.update("hil_requests", str(hil_id), {
            "user_response": response,
            "status": "answered",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        })

    async def list_runs(self, filters: dict | None = None) -> list[dict]:
        where = ""
        params: tuple = ()
        if filters:
            clauses = []
            vals: list = []
            if filters.get("uc_name"):
                clauses.append("document_set_id IN (SELECT id FROM uc_document_sets WHERE uc_name LIKE ?)")
                vals.append(f"%{filters['uc_name']}%")
            if filters.get("status"):
                clauses.append("status = ?")
                vals.append(filters["status"])
            where = " AND ".join(clauses)
            params = tuple(vals)
        return await self.db.query("pipeline_runs", where=where, params=params,
                                    order_by="started_at DESC")

    async def _execute_pipeline(self, run: PipelineRun, doc_set: UCDocumentSet) -> None:
        """Execute all 9 pipeline steps sequentially."""
        try:
            run.status = PipelineStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
            await self.db.update("pipeline_runs", str(run.id), {
                "status": run.status.value,
                "started_at": run.started_at.isoformat(),
            })

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
                "ciq_file_path": doc_set.ciq_file_path,
            }

            for step_num in range(1, 10):
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

                # Execute with retry
                async def _exec(**kwargs):
                    return (await agent.execute(
                        input_data=step_data,
                        config=self.config,
                        few_shot_examples=few_shot_examples if step_num <= 6 else None,
                        **kwargs,
                    )).model_dump()

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
                        step_data["intermediate_repr"] = step_output
                        run.intermediate_repr = step_output
                    elif step_num <= 6:
                        artifacts = step_data.get("artifacts", {})
                        artifacts.update(step_output)
                        step_data["artifacts"] = artifacts
                    elif step_num == 8:
                        step_data["test_results"] = step_output
                    elif step_num == 9:
                        step_data["report_paths"] = step_output

                    # Save artifacts
                    for artifact_path in output.get("artifacts", []):
                        if Path(artifact_path).exists():
                            content_hash = self.artifact_store.content_hash(artifact_path)
                            await self.db.insert_model("generated_configs", GeneratedConfig(
                                run_id=run.id,
                                config_type=self._infer_config_type(artifact_path),
                                file_path=artifact_path,
                                schema_valid=step_num >= 7,
                                generated_by_step=step_num,
                                content_hash=content_hash,
                            ).model_dump())
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

    def _create_agents(self) -> dict:
        from src.agents.doc_parser_agent import DocParserAgent
        from src.agents.uc_analyzer_agent import UCAnalyzerAgent
        from src.agents.validation_gen_agent import ValidationGenAgent
        from src.agents.json_template_agent import JSONTemplateAgent
        from src.agents.workflow_gen_agent import WorkflowGenAgent
        from src.agents.script_gen_agent import ScriptGenAgent
        from src.agents.schema_validator import SchemaValidator
        from src.agents.test_executor import TestExecutor
        from src.agents.report_generator import ReportGenerator

        return {
            1: DocParserAgent(), 2: UCAnalyzerAgent(),
            3: ValidationGenAgent(), 4: JSONTemplateAgent(),
            5: WorkflowGenAgent(), 6: ScriptGenAgent(),
            7: SchemaValidator(), 8: TestExecutor(),
            9: ReportGenerator(),
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
    def _infer_config_type(path: str) -> str:
        name = Path(path).stem.lower()
        if "validation" in name:
            return "validation_rules"
        if "json_template" in name or "template" in name:
            return "json_template"
        if "workflow" in name:
            return "workflow"
        if "simulator" in name:
            return "simulator_config"
        if name.endswith(".py"):
            return "python_script"
        return "unknown"
