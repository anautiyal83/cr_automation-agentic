"""Pipeline progress tracking — polished 9-step visual tracker."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

from nicegui import ui, context

from src.agents import STEP_NAMES
from src.core.events import PipelineEvent, event_bus

STATUS_CONFIG = {
    "pending":     {"color": "#94A3B8", "icon": "radio_button_unchecked", "text": "#94A3B8"},
    "in_progress": {"color": "#6C63FF", "icon": "sync",                  "text": "#6C63FF"},
    "passed":      {"color": "#16A34A", "icon": "check_circle",          "text": "#16A34A"},
    "failed":      {"color": "#DC2626", "icon": "cancel",                "text": "#DC2626"},
    "retrying":    {"color": "#D97706", "icon": "replay",                "text": "#D97706"},
    "skipped":     {"color": "#94A3B8", "icon": "skip_next",             "text": "#94A3B8"},
}

STEP_ICONS = {
    1: "file_open", 2: "analytics", 3: "fact_check",
    4: "data_object", 5: "science", 6: "account_tree",
    7: "description", 8: "code", 9: "play_circle", 10: "summarize",
}

TOTAL_STEPS = 10


class ProgressSection:
    def __init__(self, orchestrator=None, on_view_details=None, on_view_outputs=None):
        self.orchestrator = orchestrator
        self.on_view_details = on_view_details  # callback(run_id)
        self.on_view_outputs = on_view_outputs  # callback(run_id)
        self.active_run_id: UUID | None = None
        self.step_widgets: dict[int, dict] = {}
        # Capture NiceGUI client for background event updates
        try:
            self._client = context.client
        except Exception:
            self._client = None
        self._build_ui()
        # Check for active runs on load
        if orchestrator:
            ui.timer(0.5, self._restore_active_run, once=True)

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Status banner
            self.banner = ui.element("div").classes("w-full p-4 rounded-xl cr-card-inner").style(
                "background: rgba(108,99,255,0.06)"
            )
            with self.banner:
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-3"):
                        self.status_icon = ui.icon("hourglass_empty", size="28px") \
                            .classes("cr-text-dim")
                        with ui.column().classes("gap-0"):
                            self.status_label = ui.label("No active pipeline run") \
                                .classes("text-subtitle1 font-semibold")
                            self.status_detail = ui.label("Start a new run from the upload section") \
                                .classes("text-caption cr-text-dim")
                    with ui.row().classes("gap-1") as self.action_btns:
                        self.restart_btn = ui.button("Restart", icon="restart_alt",
                            on_click=self._restart) \
                            .props("flat color=deep-purple-6 size=sm no-caps")
                        self.stop_btn = ui.button("Stop", icon="pause",
                            on_click=self._stop) \
                            .props("flat color=warning size=sm no-caps")
                        self.cancel_btn = ui.button("Cancel", icon="cancel",
                            on_click=self._cancel) \
                            .props("flat color=red-4 size=sm no-caps")
                    self.action_btns.set_visibility(False)

            # Progress bar
            self.progress_bar = ui.linear_progress(value=0, show_value=False) \
                .props("color=deep-purple-6 stripe animate rounded size=8px")
            self.progress_bar.set_visibility(False)

            # Step tracker — single-row compact timeline
            with ui.row().classes("w-full gap-0 justify-between py-1 px-2"):
                for i in range(1, 11):
                    self._step_widget(i)
                    if i < 10:
                        connector = ui.element("div").classes("cr-step-connector")
                        self.step_widgets[i]["connector"] = connector

            # Error / retry detail area
            self.detail_container = ui.column().classes("w-full")

            # Milestone review panel (hidden until milestone reached)
            self.milestone_row = ui.column().classes("w-full gap-3 mt-2")
            self.milestone_row.set_visibility(False)
            with self.milestone_row:
                # Artifact info row
                with ui.row().classes("w-full items-center gap-3 cr-card-inner p-3"):
                    ui.icon("description", size="20px").classes("cr-text-accent")
                    self.ms_artifact_label = ui.label("").classes("text-body2 font-semibold flex-1")
                    self.ms_view_btn = ui.button("View Artifact", icon="visibility",
                        on_click=self._show_artifact_dialog) \
                        .props("flat color=deep-purple-6 size=sm no-caps")
                # Extra artifact files (view/download) — populated dynamically
                self.ms_extra_files_row = ui.row().classes("w-full items-center gap-3 cr-card-inner p-3")
                self.ms_extra_files_row.set_visibility(False)
                # Action buttons
                with ui.row().classes("w-full gap-3"):
                    self.ms_continue_btn = ui.button("Continue", icon="play_arrow",
                        on_click=lambda: self._milestone_respond("continue")) \
                        .props("unelevated color=positive size=sm no-caps")
                    self.ms_skip_btn = ui.button("Skip & Continue", icon="skip_next",
                        on_click=lambda: self._milestone_respond("continue")) \
                        .props("unelevated color=warning size=sm no-caps")
                    self.ms_skip_btn.set_visibility(False)
                    self.ms_rerun_btn = ui.button("Re-run Step", icon="replay",
                        on_click=lambda: self._milestone_respond("rerun")) \
                        .props("unelevated color=deep-purple-6 size=sm no-caps")
                    self.ms_cancel_btn = ui.button("Cancel Pipeline", icon="stop",
                        on_click=lambda: self._milestone_respond("cancel")) \
                        .props("unelevated color=negative size=sm no-caps")

            # Completion actions (hidden until pipeline finishes)
            self.completion_row = ui.row().classes("w-full items-center gap-3 mt-2")
            self.completion_row.set_visibility(False)
            with self.completion_row:
                self.btn_details = ui.button("View Full Details", icon="info_outline",
                                             on_click=self._go_details) \
                    .props("unelevated color=deep-purple-6 size=sm no-caps")
                self.btn_outputs = ui.button("Browse Outputs", icon="source",
                                             on_click=self._go_outputs) \
                    .props("unelevated color=teal-6 size=sm no-caps")

    # Short names for compact single-row display
    _SHORT_NAMES = {
        1: "DOC Parser", 2: "DOC Analyzer", 3: "CIQ Config Gen",
        4: "CIQ JSON Config", 5: "QA-CIQ Validation", 6: "Execution Config",
        7: "QA-MOP Gen", 8: "Simulator Setup", 9: "QA-MOP Exec",
        10: "Final Report",
    }

    def _step_widget(self, step_num: int):
        with ui.column().classes("items-center gap-0 flex-shrink-0 flex-1") as col:
            # Circle icon
            icon_container = ui.element("div").classes("cr-step-circle")
            with icon_container:
                step_icon = ui.icon(
                    STEP_ICONS.get(step_num, "circle"), size="14px"
                ).classes("cr-text-dim")

            # Step label
            label = ui.label(f"{step_num}").classes("text-[9px] cr-text-dim font-semibold")
            name = ui.label(self._SHORT_NAMES.get(step_num, "")) \
                .classes("text-[9px] cr-text-dim text-center") \
                .style("white-space: nowrap; overflow: visible")
            status_label = ui.label("pending") \
                .classes("text-[9px] cr-text-dim") \
                .style("white-space: nowrap")

            self.step_widgets[step_num] = {
                "col": col, "icon_container": icon_container, "step_icon": step_icon,
                "label": label, "name": name, "status": status_label,
            }

    async def _restore_active_run(self):
        """Check DB for any running/paused pipeline and restore the progress view."""
        try:
            if not self.orchestrator or not self.orchestrator.db:
                return
            db = self.orchestrator.db
            rows = await db.execute(
                "SELECT r.id, r.status, r.current_step, d.uc_name "
                "FROM pipeline_runs r "
                "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id "
                "WHERE r.status IN ('running', 'paused_hil', 'paused_milestone', "
                "'PipelineStatus.RUNNING', 'PipelineStatus.PAUSED_HIL', 'PipelineStatus.PAUSED_MILESTONE') "
                "ORDER BY r.started_at DESC LIMIT 1", ()
            )
            if not rows:
                return

            run = rows[0]
            run_id = UUID(run["id"])
            status = str(run.get("status", "")).replace("PipelineStatus.", "").lower()
            step = run.get("current_step", 0)
            uc_name = run.get("uc_name", "")

            self.active_run_id = run_id
            self.action_btns.set_visibility(True)
            self.progress_bar.set_visibility(True)

            # Restore step visuals from DB
            step_rows = await db.execute(
                "SELECT step_number, status FROM step_results WHERE run_id = ? ORDER BY step_number",
                (str(run_id),)
            )
            for sr in step_rows:
                sn = sr.get("step_number", 0)
                ss = str(sr.get("status", "pending")).replace("StepStatus.", "").lower()
                if sn and sn in self.step_widgets:
                    self._update_step_visual(sn, ss)

            # Set banner based on status
            if status == "running":
                self.status_icon.name = "sync"
                self.status_icon.classes(replace="cr-text-accent cr-spin")
                self.status_label.text = f"Pipeline Running — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS}: {STEP_NAMES.get(step, '')}"
                self.progress_bar.value = (step - 1) / TOTAL_STEPS if step > 0 else 0
                self.progress_bar.props("color=deep-purple-6 stripe animate")
                self.banner.style(replace=(
                    "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
                ))
                event_bus.subscribe(self._on_event, run_id)
            elif status == "paused_milestone":
                self.status_icon.name = "rate_review"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = f"Milestone Review — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS} completed — awaiting review"
                self.progress_bar.value = step / TOTAL_STEPS
                self.progress_bar.props("color=deep-purple-6")
                self.progress_bar.props(remove="animate")
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))

                # Show milestone review buttons
                milestone_name = self.orchestrator.MILESTONE_NAMES.get(step, f"Step {step}")
                run_dir = self.orchestrator.artifact_store.run_dir(run_id)
                # Try to find the artifact file
                artifact_file = ""
                artifact_map = {
                    3: f"{uc_name}_validation-rules.yaml",
                    4: f"{uc_name}_json-output.yaml",
                    5: "qa/qa-execution-report.html",
                    6: f"{uc_name}.yaml",
                    7: f"{uc_name}_mop-generation-report.html",
                    8: f"{uc_name}_simulator-overview.html",
                    9: f"{uc_name}_exec-test-report.html",
                }
                fname = artifact_map.get(step, "")
                if fname:
                    from pathlib import Path
                    candidate = Path(run_dir) / fname
                    if candidate.exists():
                        artifact_file = str(candidate)
                # For test steps, check if there were failures
                has_failures = False
                if step in (5, 8):
                    try:
                        step_rows = await db.execute(
                            "SELECT status FROM step_results WHERE run_id = ? AND step_number = ?",
                            (str(run_id), step),
                        )
                        if step_rows and "failed" in str(step_rows[0].get("status", "")).lower():
                            has_failures = True
                    except Exception:
                        pass

                self._show_milestone_review(step, milestone_name, artifact_file,
                                            has_failures=has_failures)
                event_bus.subscribe(self._on_event, run_id)
            elif status == "paused_hil":
                self.status_icon.name = "help_outline"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = f"Waiting for Input — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS} needs human clarification"
                self.progress_bar.value = (step - 1) / TOTAL_STEPS
                self.progress_bar.props("color=warning")
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))
                event_bus.subscribe(self._on_event, run_id)
        except Exception:
            pass

    async def start_tracking(self, run_id: UUID):
        self.active_run_id = run_id
        self.action_btns.set_visibility(True)
        self.completion_row.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 0
        self.progress_bar.props("color=deep-purple-6 stripe animate indeterminate")
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-accent cr-spin")
        self.status_label.text = "Pipeline Starting..."
        self.status_detail.text = "Initializing agents and loading models — this may take a few seconds"
        self.banner.style(replace=(
            "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
        ))
        # Reset all steps
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")
        event_bus.subscribe(self._on_event, run_id)

    async def _on_event(self, event: PipelineEvent):
        # Ignore events if run was already cleared (cancelled/stopped)
        if not self.active_run_id:
            return
        # Run UI updates within NiceGUI client context (needed for background task events)
        if self._client:
            try:
                with self._client:
                    await self._handle_event(event)
            except Exception:
                await self._handle_event(event)
        else:
            await self._handle_event(event)

    async def _handle_event(self, event: PipelineEvent):
        if event.event_type == "step_started":
            self._update_step_visual(event.step_number, "in_progress")
            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Running"
            self.status_detail.text = f"Step {event.step_number}/{TOTAL_STEPS}: {STEP_NAMES.get(event.step_number, '')}"
            self.progress_bar.props(remove="indeterminate")
            self.progress_bar.value = (event.step_number - 1) / TOTAL_STEPS
            self.milestone_row.set_visibility(False)
        elif event.event_type == "step_completed":
            self._update_step_visual(event.step_number, event.payload.get("status", "passed"))
            self.progress_bar.value = event.step_number / TOTAL_STEPS
        elif event.event_type == "step_failed":
            self._update_step_visual(event.step_number, "failed")
        elif event.event_type == "retry_started":
            n = event.payload.get("attempt", 1)
            self._update_step_visual(event.step_number, "retrying")
            self.status_detail.text = f"Retrying step {event.step_number} (attempt {n})..."
        elif event.event_type == "milestone_reached":
            milestone = event.payload.get("milestone", "")
            artifact_file = event.payload.get("artifact_file", "")
            test_status = event.payload.get("test_status")

            # Step 5 test results: show pass/fail visual
            has_failures = test_status == "failed"
            if has_failures:
                self._update_step_visual(event.step_number, "failed")
                self.status_icon.name = "error_outline"
                self.status_icon.classes(replace="cr-text-error")
                if event.step_number in (5, 7, 9):
                    self.status_detail.text = "Tests failed — re-run to fix, or skip to continue."
                else:
                    self.status_detail.text = "Tests failed — review results below."
                self.banner.style(replace=(
                    "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
                ))
            else:
                self._update_step_visual(event.step_number, "passed")
                self.status_icon.name = "rate_review"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_detail.text = "Review the generated artifact below, then continue, re-run, or cancel."
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))

            self.progress_bar.value = event.step_number / TOTAL_STEPS
            self.progress_bar.props(remove="animate")
            self.status_label.text = f"Review: {milestone}"
            extra_files = event.payload.get("extra_artifact_files", [])
            self._show_milestone_review(event.step_number, milestone, artifact_file,
                                        has_failures=has_failures,
                                        extra_artifact_files=extra_files)
        elif event.event_type == "run_completed":
            self.status_icon.name = "check_circle"
            self.status_icon.classes(replace="cr-text-success")
            self.status_label.text = "Pipeline Completed"
            dur = event.payload.get("duration", 0)
            self.status_detail.text = f"All 9 steps completed in {dur:.0f}s"
            self.banner.style(replace=(
                "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3)"
            ))
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.progress_bar.value = 1.0
            self.progress_bar.props("color=positive")
        elif event.event_type == "run_failed":
            self.status_icon.name = "error"
            self.status_icon.classes(replace="cr-text-error")
            self.status_label.text = "Pipeline Failed"
            self.status_detail.text = event.payload.get("error", "Unknown error")
            self.banner.style(replace=(
                "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
            ))
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.progress_bar.props("color=negative")
            self.progress_bar.props(remove="stripe animate")
        elif event.event_type == "run_cancelled":
            self._reset_to_default()
            self.progress_bar.props("color=warning")
            self.progress_bar.props(remove="stripe animate")

    def _update_step_visual(self, step_num: int, status: str):
        if not step_num or step_num not in self.step_widgets:
            return
        base = status.split("(")[0].strip() if "(" in str(status) else str(status)
        cfg = STATUS_CONFIG.get(base, STATUS_CONFIG["pending"])

        w = self.step_widgets[step_num]
        active_cls = "cr-step-circle-active" if base == "in_progress" else ""
        w["icon_container"].classes(replace=f"cr-step-circle {active_cls}")
        w["icon_container"].style(replace=f"border-color: {cfg['color']}")
        w["step_icon"].name = cfg["icon"]
        w["step_icon"].classes(replace=f"text-[{cfg['text']}]")
        w["status"].text = status
        w["status"].classes(replace=f"text-[10px] text-[{cfg['text']}] font-semibold")

        # Update connector line color for completed steps
        if base in ("passed", "in_progress") and "connector" in w:
            w["connector"].style(replace=f"background: {cfg['color']}")

    def _show_milestone_review(self, step_num: int, milestone: str, artifact_file: str,
                               has_failures: bool = False,
                               extra_artifact_files: list[str] | None = None):
        self.milestone_row.set_visibility(True)
        self.action_btns.set_visibility(False)
        self._current_artifact_file = artifact_file
        self._current_milestone = milestone
        self._current_milestone_step = step_num
        # Update rerun button label for test steps
        if step_num == 5:
            self.ms_rerun_btn.text = "Re-run Steps 3-5"
        elif step_num == 7:
            self.ms_rerun_btn.text = "Re-run Steps 6-9"
        elif step_num == 9:
            self.ms_rerun_btn.text = "Re-run Steps 6-9"
        else:
            self.ms_rerun_btn.text = "Re-run Step"

        # For test steps with failures: disable Continue, show Skip & Continue
        if has_failures and step_num in (5, 7, 9):
            self.ms_continue_btn.set_visibility(False)
            self.ms_skip_btn.set_visibility(True)
        else:
            self.ms_continue_btn.set_visibility(True)
            self.ms_skip_btn.set_visibility(False)

        from pathlib import Path
        fname = Path(artifact_file).name if artifact_file else "no artifact"
        self.ms_artifact_label.text = f"{milestone}  —  {fname}"
        self.ms_view_btn.set_visibility(bool(artifact_file))

        # Render extra artifact files (view + download)
        self.ms_extra_files_row.clear()
        if extra_artifact_files:
            from nicegui import app as nicegui_app
            with self.ms_extra_files_row:
                for fpath in extra_artifact_files:
                    if fpath and Path(fpath).exists():
                        ef_name = Path(fpath).name
                        try:
                            serve_url = nicegui_app.add_media_file(local_file=fpath)
                        except Exception:
                            serve_url = ""
                        ui.icon("article", size="18px").classes("cr-text-accent")
                        ui.label(ef_name).classes("text-body2 font-mono")
                        if serve_url:
                            ui.button("View", icon="visibility",
                                      on_click=lambda u=serve_url: ui.navigate.to(u, new_tab=True)) \
                                .props("flat color=deep-purple-6 size=sm no-caps")
                            ui.button("Download", icon="download",
                                      on_click=lambda u=serve_url: ui.download(u)) \
                                .props("flat color=positive size=sm no-caps")
            self.ms_extra_files_row.set_visibility(True)
        else:
            self.ms_extra_files_row.set_visibility(False)

    def _show_artifact_dialog(self):
        import html as html_mod
        from pathlib import Path
        from nicegui import app

        fpath = getattr(self, "_current_artifact_file", "")
        milestone = getattr(self, "_current_milestone", "Artifact")
        is_html = fpath.endswith(".html") if fpath else False

        dialog = ui.dialog().props("maximized")
        with dialog, ui.card().classes("w-full h-full p-0").style(
            "display: flex; flex-direction: column; max-height: 100vh"
        ):
            # Header bar
            with ui.row().classes("items-center justify-between w-full px-4 py-2").style(
                "border-bottom: 1px solid var(--cr-border); flex-shrink: 0"
            ):
                ui.label(f"{milestone} — {Path(fpath).name if fpath else ''}") \
                    .classes("text-subtitle2 font-bold font-mono")
                with ui.row().classes("items-center gap-1"):
                    if fpath and Path(fpath).exists():
                        dl_url = app.add_media_file(local_file=fpath)
                        ui.button("Download", icon="download",
                                  on_click=lambda u=dl_url: ui.download(u)) \
                            .props("flat color=positive size=sm no-caps")
                    ui.button(icon="close", on_click=dialog.close) \
                        .props("flat round size=sm color=grey-6")

            # Content
            if fpath and Path(fpath).exists():
                if is_html:
                    # Serve the file and show in iframe (no scroll_area wrapper)
                    url = app.add_media_file(local_file=fpath)
                    ui.element("iframe").props(f'src="{url}"').style(
                        "width: 100%; flex: 1; border: none; min-height: 0"
                    )
                else:
                    content = Path(fpath).read_text(encoding="utf-8")
                    with ui.scroll_area().style("flex: 1; min-height: 0"):
                        escaped = html_mod.escape(content)
                        ui.html(
                            f'<pre style="margin:0; padding: 16px; font-size: 12px; '
                            f'line-height: 1.5; font-family: monospace">{escaped}</pre>'
                        )
            else:
                ui.label("No content available").classes("p-4 cr-text-dim")
        dialog.open()

    async def _milestone_respond(self, decision: str):
        self.milestone_row.set_visibility(False)
        if not self.orchestrator or not self.active_run_id:
            return

        # Live check: is the pipeline task actually waiting for our response?
        has_live_event = self.active_run_id in self.orchestrator._milestone_events
        if not has_live_event:
            # Pipeline task is gone (server restart) — resume from correct step
            if decision == "cancel":
                await self.orchestrator.db.update("pipeline_runs", str(self.active_run_id), {
                    "status": "cancelled",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                self._reset_to_default()
                ui.notify("Pipeline cancelled", type="warning")
                return
            else:
                # Determine resume step based on current milestone and decision
                run_row = await self.orchestrator.db.get_by_id(
                    "pipeline_runs", str(self.active_run_id))
                current_step = run_row.get("current_step", 1) if run_row else 1

                if decision == "rerun":
                    # Go back to regenerate: step 5→3, step 7/9→6, others→same step
                    resume_step = {5: 3, 7: 6, 9: 6}.get(current_step, current_step)
                else:
                    # Continue: resume from next step
                    resume_step = current_step + 1

                try:
                    resumed = await self.orchestrator.resume_run(
                        self.active_run_id, from_step=resume_step)
                    await self.start_tracking(resumed.id)
                    ui.notify(f"Pipeline resumed from step {resume_step}", type="positive")
                except Exception as exc:
                    ui.notify(f"Resume failed: {exc}", type="negative")
                return

        await self.orchestrator.respond_to_milestone(self.active_run_id, decision)
        if decision == "continue":
            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Running"
            self.status_detail.text = "Continuing..."
            self.banner.style(replace=(
                "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
            ))
            self.progress_bar.props("stripe animate")
            self.action_btns.set_visibility(True)
        elif decision == "rerun":
            self.status_icon.name = "replay"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            ms_step = getattr(self, "_current_milestone_step", 0)
            if ms_step == 5:
                self.status_label.text = "Re-running Steps 3-5"
                self.status_detail.text = "Re-generating validation rules, JSON output, then re-testing..."
                for s in (3, 4, 5):
                    self._update_step_visual(s, "pending")
            elif ms_step in (7, 9):
                self.status_label.text = "Re-running Steps 6-9"
                self.status_detail.text = "Re-generating workflow, MOP, simulator config, then re-testing..."
                for s in (6, 7, 8, 9):
                    self._update_step_visual(s, "pending")
            else:
                self.status_label.text = "Re-running Step"
                self.status_detail.text = "Re-generating artifact..."
            self.banner.style(replace=(
                "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
            ))
            self.progress_bar.props("stripe animate")
            self.action_btns.set_visibility(True)
        elif decision == "cancel":
            # Fully reset UI — don't rely on run_cancelled event
            self.status_icon.name = "cancel"
            self.status_icon.classes(replace="cr-text-error")
            self.status_label.text = "Pipeline Cancelled"
            self.status_detail.text = "Cancelled by user at milestone review"
            self.banner.style(replace=(
                "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
            ))
            self.progress_bar.props("color=negative")
            self.progress_bar.props(remove="stripe animate")
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.active_run_id = None

    def _reset_to_default(self):
        """Reset progress section to default 'No active pipeline' state."""
        self.active_run_id = None
        self.action_btns.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.progress_bar.set_visibility(False)
        self.status_icon.name = "hourglass_empty"
        self.status_icon.classes(replace="cr-text-dim")
        self.status_label.text = "No active pipeline run"
        self.status_detail.text = "Start a new run from the upload section"
        self.banner.style(replace="background: rgba(108,99,255,0.06)")
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")

    async def _restart(self):
        """Cancel current run and re-run with same documents."""
        if not self.orchestrator or not self.active_run_id:
            return
        run_id = self.active_run_id
        try:
            # Cancel current
            self._reset_to_default()
            await self.orchestrator.cancel_run(run_id)
            # Re-run with same doc set
            new_run = await self.orchestrator.rerun(run_id)
            await self.start_tracking(new_run.id)
            ui.notify("Pipeline restarted from beginning", type="positive")
        except Exception as exc:
            ui.notify(f"Restart failed: {exc}", type="negative")

    async def _stop(self):
        """Stop the pipeline — preserves completed artifacts, can be resumed later."""
        if not self.orchestrator or not self.active_run_id:
            return
        run_id = self.active_run_id
        self.active_run_id = None

        # Show stopping state
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-warning cr-spin")
        self.status_label.text = "Stopping Pipeline..."
        self.status_detail.text = "Waiting for current step to finish"
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 1.0
        self.progress_bar.props("color=warning stripe animate")

        try:
            await self.orchestrator.cancel_run(run_id)
        except Exception:
            pass

        await asyncio.sleep(2)

        self._reset_to_default()
        ui.notify("Pipeline stopped — use Re-run from History to resume", type="warning")

    async def _cancel(self):
        """Cancel the pipeline — shows cancelling state, then resets."""
        if not self.orchestrator or not self.active_run_id:
            return
        run_id = self.active_run_id
        self.active_run_id = None  # prevent events from overwriting

        # Show cancelling state
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-error cr-spin")
        self.status_label.text = "Cancelling Pipeline..."
        self.status_detail.text = "Waiting for background tasks to stop"
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 1.0
        self.progress_bar.props("color=negative stripe animate")

        # Cancel backend
        try:
            await self.orchestrator.cancel_run(run_id)
        except Exception:
            pass

        # Show cancelling state for at least 2 seconds
        await asyncio.sleep(2)

        self._reset_to_default()
        ui.notify("Pipeline cancelled", type="negative")

    async def _go_details(self):
        if self.active_run_id and self.on_view_details:
            await self.on_view_details(str(self.active_run_id))

    async def _go_outputs(self):
        if self.active_run_id and self.on_view_outputs:
            await self.on_view_outputs(str(self.active_run_id))
