"""Run history section — polished filterable table."""
from __future__ import annotations

from nicegui import ui


class HistorySection:
    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Filter bar
            with ui.card().classes("w-full p-3").style(
                "background: #2A2A3E; border: 1px solid #3A3A50; border-radius: 12px"
            ):
                with ui.row().classes("w-full items-end gap-3"):
                    self.name_filter = ui.input(
                        "UC Name", placeholder="Search by name...",
                    ).classes("flex-1").props("outlined dense clearable")
                    self.status_filter = ui.select(
                        {"all": "All Statuses", "completed": "Completed",
                         "failed": "Failed", "cancelled": "Cancelled", "running": "Running"},
                        value="all", label="Status",
                    ).props("outlined dense").classes("min-w-36")
                    ui.button("Search", icon="search", on_click=self._refresh) \
                        .props("unelevated color=deep-purple-6 size=sm")

            # Results table
            self.table = ui.table(
                columns=[
                    {"name": "uc_name", "label": "UC Name", "field": "uc_name",
                     "sortable": True, "align": "left"},
                    {"name": "status", "label": "Status", "field": "status",
                     "sortable": True, "align": "center"},
                    {"name": "started_at", "label": "Started", "field": "started_at",
                     "sortable": True, "align": "left"},
                    {"name": "duration", "label": "Duration (s)", "field": "duration_seconds",
                     "align": "right"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full").props(
                'flat bordered separator=cell '
                'table-header-class="bg-[#2A2A3E] text-[#9090A8]" '
                'no-data-label="No pipeline runs found"'
            )

            # Empty state
            with ui.row().classes("w-full justify-center py-6"):
                with ui.column().classes("items-center gap-2"):
                    ui.icon("inbox", size="48px").classes("text-[#3A3A50]")
                    ui.label("No runs yet").classes("text-body2 text-[#9090A8]")
                    ui.label("Complete a pipeline run to see it here") \
                        .classes("text-caption text-[#3A3A50]")

    async def _refresh(self):
        if not self.orchestrator:
            return
        filters = {}
        if self.name_filter.value:
            filters["uc_name"] = self.name_filter.value
        if self.status_filter.value != "all":
            filters["status"] = self.status_filter.value
        runs = await self.orchestrator.list_runs(filters)
        self.table.rows = runs
