"""Artifact cleanup section — polished selection UI."""
from __future__ import annotations

from uuid import UUID

from nicegui import ui


STATUS_COLORS = {
    "completed": "#4ADE80", "failed": "#F87171",
    "cancelled": "#FBBF24", "running": "#6C63FF", "pending": "#9090A8",
}


class CleanupSection:
    def __init__(self, orchestrator=None, artifact_store=None):
        self.orchestrator = orchestrator
        self.artifact_store = artifact_store
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Select pipeline runs to clean up artifacts") \
                    .classes("text-body2 text-[#9090A8]")
                ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                    .props("flat color=grey-5 size=sm")

            self.runs_container = ui.column().classes("w-full gap-2")

            # Empty state
            with self.runs_container:
                with ui.row().classes("w-full justify-center py-8"):
                    with ui.column().classes("items-center gap-2"):
                        ui.icon("folder_off", size="48px").classes("text-[#3A3A50]")
                        ui.label("No runs to clean up").classes("text-body2 text-[#9090A8]")

    async def _refresh(self):
        self.runs_container.clear()
        if not self.orchestrator:
            return
        runs = await self.orchestrator.list_runs()
        if not runs:
            with self.runs_container:
                with ui.row().classes("w-full justify-center py-8"):
                    ui.icon("folder_off", size="48px").classes("text-[#3A3A50]")
                    ui.label("No runs to clean up").classes("text-body2 text-[#9090A8]")
            return

        with self.runs_container:
            for run in runs[:50]:
                run_id = run.get("id", "")
                status = run.get("status", "unknown")
                color = STATUS_COLORS.get(status, "#9090A8")

                with ui.card().classes("w-full p-3").style(
                    f"background: #2A2A3E; border: 1px solid #3A3A50; border-radius: 10px"
                ):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("circle", size="12px").style(f"color: {color}")
                            with ui.column().classes("gap-0"):
                                ui.label(f"Run {run_id[:8]}...") \
                                    .classes("text-subtitle2 font-medium")
                                ui.label(f"{status} | {run.get('started_at', 'N/A')[:19]}") \
                                    .classes("text-caption text-[#9090A8]")
                        ui.button("Delete", icon="delete_outline",
                                  on_click=lambda _, r=run_id: self._delete(r)) \
                            .props("flat color=red-4 size=sm")

    async def _delete(self, run_id: str):
        with ui.dialog() as dialog, ui.card().classes("p-4").style(
            "background: #1E1E2E; border: 1px solid #3A3A50; border-radius: 12px"
        ):
            ui.label("Confirm Deletion").classes("text-subtitle1 font-semibold")
            ui.label(f"Delete all artifacts for run {run_id[:8]}...?") \
                .classes("text-body2 text-[#9090A8] my-2")
            ui.label("This action cannot be undone.").classes("text-caption text-[#F87171]")
            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat color=grey-5")

                async def confirm():
                    if self.artifact_store:
                        self.artifact_store.delete_run(UUID(run_id))
                    dialog.close()
                    await self._refresh()
                    ui.notify("Artifacts deleted", type="positive")

                ui.button("Delete", icon="delete", on_click=confirm) \
                    .props("unelevated color=red-6")
        dialog.open()
