"""Pipeline progress tracking — polished 9-step visual tracker."""
from __future__ import annotations

from uuid import UUID

from nicegui import ui

from src.agents import STEP_NAMES
from src.core.events import PipelineEvent, event_bus

STATUS_CONFIG = {
    "pending":     {"color": "#3A3A50", "icon": "radio_button_unchecked", "text": "#9090A8"},
    "in_progress": {"color": "#6C63FF", "icon": "sync",                  "text": "#6C63FF"},
    "passed":      {"color": "#4ADE80", "icon": "check_circle",          "text": "#4ADE80"},
    "failed":      {"color": "#F87171", "icon": "cancel",                "text": "#F87171"},
    "retrying":    {"color": "#FBBF24", "icon": "replay",                "text": "#FBBF24"},
    "skipped":     {"color": "#9090A8", "icon": "skip_next",             "text": "#9090A8"},
}

STEP_ICONS = {
    1: "file_open", 2: "analytics", 3: "fact_check",
    4: "data_object", 5: "account_tree", 6: "code",
    7: "verified", 8: "science", 9: "summarize",
}


class ProgressSection:
    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self.active_run_id: UUID | None = None
        self.step_widgets: dict[int, dict] = {}
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Status banner
            self.banner = ui.element("div").classes("w-full p-4 rounded-xl").style(
                "background: rgba(108,99,255,0.06); border: 1px solid #3A3A50"
            )
            with self.banner:
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-3"):
                        self.status_icon = ui.icon("hourglass_empty", size="28px") \
                            .classes("text-[#9090A8]")
                        with ui.column().classes("gap-0"):
                            self.status_label = ui.label("No active pipeline run") \
                                .classes("text-subtitle1 font-semibold")
                            self.status_detail = ui.label("Start a new run from the upload section") \
                                .classes("text-caption text-[#9090A8]")
                    self.cancel_btn = ui.button("Cancel", icon="stop") \
                        .props("flat color=red-4 size=sm") \
                        .on("click", self._cancel)
                    self.cancel_btn.set_visibility(False)

            # Step tracker — horizontal timeline
            with ui.row().classes("w-full gap-0 overflow-x-auto py-2"):
                for i in range(1, 10):
                    self._step_widget(i)
                    if i < 9:
                        # Connector line
                        connector = ui.element("div").style(
                            "width: 20px; height: 2px; background: #3A3A50; "
                            "margin-top: 20px; flex-shrink: 0"
                        )
                        self.step_widgets[i]["connector"] = connector

            # Error / retry detail area
            self.detail_container = ui.column().classes("w-full")

    def _step_widget(self, step_num: int):
        with ui.column().classes("items-center gap-1 flex-shrink-0") \
                .style("min-width: 80px") as col:
            # Circle icon
            icon_container = ui.element("div").style(
                "width: 40px; height: 40px; border-radius: 50%; display: flex; "
                "align-items: center; justify-content: center; "
                "background: #2A2A3E; border: 2px solid #3A3A50; transition: all 0.3s ease"
            )
            with icon_container:
                step_icon = ui.icon(
                    STEP_ICONS.get(step_num, "circle"), size="18px"
                ).classes("text-[#9090A8]")

            # Step label
            label = ui.label(f"Step {step_num}").classes("text-[10px] text-[#9090A8] font-semibold")
            name = ui.label(STEP_NAMES.get(step_num, "")[:12]) \
                .classes("text-[10px] text-[#9090A8] text-center leading-tight") \
                .style("max-width: 80px")
            status_label = ui.label("pending") \
                .classes("text-[10px] text-[#9090A8]")

            self.step_widgets[step_num] = {
                "col": col, "icon_container": icon_container, "step_icon": step_icon,
                "label": label, "name": name, "status": status_label,
            }

    async def start_tracking(self, run_id: UUID):
        self.active_run_id = run_id
        self.cancel_btn.set_visibility(True)
        self.status_icon.name = "sync"
        self.status_icon.classes(replace="text-[#6C63FF]")
        self.status_label.text = "Pipeline Running"
        self.status_detail.text = "Processing documents through the 9-step agent pipeline..."
        self.banner.style(replace=(
            "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
        ))
        # Reset all steps
        for i in range(1, 10):
            self._update_step_visual(i, "pending")
        event_bus.subscribe(self._on_event, run_id)

    async def _on_event(self, event: PipelineEvent):
        if event.event_type == "step_started":
            self._update_step_visual(event.step_number, "in_progress")
            self.status_detail.text = f"Step {event.step_number}: {STEP_NAMES.get(event.step_number, '')}"
        elif event.event_type == "step_completed":
            self._update_step_visual(event.step_number, event.payload.get("status", "passed"))
        elif event.event_type == "step_failed":
            self._update_step_visual(event.step_number, "failed")
        elif event.event_type == "retry_started":
            n = event.payload.get("attempt", 1)
            self._update_step_visual(event.step_number, "retrying")
            self.status_detail.text = f"Retrying step {event.step_number} (attempt {n})..."
        elif event.event_type == "run_completed":
            self.status_icon.name = "check_circle"
            self.status_icon.classes(replace="text-[#4ADE80]")
            self.status_label.text = "Pipeline Completed"
            dur = event.payload.get("duration", 0)
            self.status_detail.text = f"All 9 steps completed in {dur:.0f}s"
            self.banner.style(replace=(
                "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3)"
            ))
            self.cancel_btn.set_visibility(False)
        elif event.event_type == "run_failed":
            self.status_icon.name = "error"
            self.status_icon.classes(replace="text-[#F87171]")
            self.status_label.text = "Pipeline Failed"
            self.status_detail.text = event.payload.get("error", "Unknown error")
            self.banner.style(replace=(
                "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
            ))
            self.cancel_btn.set_visibility(False)
        elif event.event_type == "run_cancelled":
            self.status_icon.name = "cancel"
            self.status_icon.classes(replace="text-[#FBBF24]")
            self.status_label.text = "Pipeline Cancelled"
            self.status_detail.text = "Completed step artifacts preserved"
            self.cancel_btn.set_visibility(False)

    def _update_step_visual(self, step_num: int, status: str):
        if not step_num or step_num not in self.step_widgets:
            return
        base = status.split("(")[0].strip() if "(" in str(status) else str(status)
        cfg = STATUS_CONFIG.get(base, STATUS_CONFIG["pending"])

        w = self.step_widgets[step_num]
        w["icon_container"].style(replace=(
            f"width: 40px; height: 40px; border-radius: 50%; display: flex; "
            f"align-items: center; justify-content: center; "
            f"background: {'rgba(108,99,255,0.15)' if base == 'in_progress' else '#2A2A3E'}; "
            f"border: 2px solid {cfg['color']}; transition: all 0.3s ease"
        ))
        w["step_icon"].name = cfg["icon"]
        w["step_icon"].classes(replace=f"text-[{cfg['text']}]")
        w["status"].text = status
        w["status"].classes(replace=f"text-[10px] text-[{cfg['text']}] font-semibold")

        # Update connector line color for completed steps
        if base in ("passed", "in_progress") and "connector" in w:
            w["connector"].style(replace=(
                f"width: 20px; height: 2px; background: {cfg['color']}; "
                f"margin-top: 20px; flex-shrink: 0"
            ))

    async def _cancel(self):
        if self.orchestrator and self.active_run_id:
            await self.orchestrator.cancel_run(self.active_run_id)
