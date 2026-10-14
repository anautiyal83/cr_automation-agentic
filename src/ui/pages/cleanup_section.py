"""Artifact cleanup section — polished selection UI."""
from __future__ import annotations

from uuid import UUID

from nicegui import ui


STATUS_COLORS = {
    "completed": "#16A34A", "failed": "#DC2626",
    "cancelled": "#D97706", "running": "#6C63FF", "pending": "#94A3B8",
}


class CleanupSection:
    def __init__(self, orchestrator=None, artifact_store=None):
        self.orchestrator = orchestrator
        self.artifact_store = artifact_store
        self._build_ui()
        if orchestrator:
            async def _safe_refresh():
                try:
                    await self._refresh()
                except RuntimeError:
                    pass
            ui.timer(0.5, _safe_refresh, once=True)

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Filter bar
            with ui.row().classes("w-full items-end gap-3"):
                self.node_filter = ui.select(
                    {"": "All Nodes"}, value="", label="Node Type",
                    on_change=lambda _: self._refresh(),
                ).props("outlined dense").classes("min-w-32")
                self.activity_filter = ui.select(
                    {"": "All Activities"}, value="", label="Activity",
                    on_change=lambda _: self._refresh(),
                ).props("outlined dense").classes("min-w-32")
                ui.element("div").classes("flex-1")
                ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                    .props("flat color=grey-5 size=sm")

            self.table = ui.table(
                columns=[
                    {"name": "uc_name", "label": "UC Name", "field": "uc_name",
                     "sortable": True, "align": "left"},
                    {"name": "status", "label": "Status", "field": "status",
                     "sortable": True, "align": "center"},
                    {"name": "started_at", "label": "Started", "field": "started_at",
                     "sortable": True, "align": "left"},
                    {"name": "actions", "label": "", "field": "actions", "align": "center"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full").props(
                'flat bordered separator=cell dense '
                'table-header-class="cr-bg-surface-light cr-text-dim" '
                'no-data-label="No runs to clean up"'
            )
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense icon="delete_outline" color="red-4" size="sm"
                           @click="$parent.$emit('delete', props.row)"
                           label="Delete" no-caps />
                </q-td>
            ''')
            self.table.on('delete', self._on_delete_click)

    async def _refresh(self):
        try:
            return await self._do_refresh()
        except RuntimeError:
            pass

    async def _do_refresh(self):
        if not self.orchestrator:
            return
        try:
            runs = await self.orchestrator.list_runs()
        except Exception:
            runs = []

        # Extract nodes and activities
        nodes = set()
        all_activities: dict[str, set] = {}
        for r in runs:
            uc = r.get("uc_name", "")
            parts = uc.split("_", 1)
            if len(parts) == 2:
                nodes.add(parts[0])
                all_activities.setdefault(parts[0], set()).add(parts[1])
                all_activities.setdefault("", set()).add(parts[1])
            elif uc:
                nodes.add(uc)

        node_opts = {"": "All Nodes"}
        node_opts.update({n: n for n in sorted(nodes)})
        self.node_filter.options = node_opts
        self.node_filter.update()

        node_val = self.node_filter.value or ""
        relevant_activities = all_activities.get(node_val, set())
        act_opts = {"": "All Activities"}
        act_opts.update({a: a for a in sorted(relevant_activities)})
        self.activity_filter.options = act_opts
        self.activity_filter.update()

        # Apply filters
        act_val = self.activity_filter.value or ""
        if node_val:
            runs = [r for r in runs if r.get("uc_name", "").startswith(node_val + "_") or r.get("uc_name", "") == node_val]
        if act_val:
            runs = [r for r in runs if r.get("uc_name", "").endswith("_" + act_val)]

        # Format and update table rows (no DOM rebuild)
        for r in runs:
            r["started_at"] = str(r.get("started_at", ""))[:19]
            r["status"] = str(r.get("status", "")).replace("PipelineStatus.", "")
        self.table.rows = runs

    async def _on_delete_click(self, e):
        row = e.args
        rid = row.get("id", "")
        uc = row.get("uc_name", rid[:8])
        if not rid:
            return

        async def do_delete():
            try:
                if self.artifact_store:
                    self.artifact_store.delete_run(UUID(rid))
                if self.orchestrator and self.orchestrator.db:
                    db = self.orchestrator.db
                    run_rows = await db.execute("SELECT document_set_id FROM pipeline_runs WHERE id = ?", (rid,))
                    doc_set_id = run_rows[0]["document_set_id"] if run_rows else None
                    await db.execute_insert("DELETE FROM retry_attempts WHERE step_result_id IN (SELECT id FROM step_results WHERE run_id = ?)", (rid,))
                    await db.execute_insert("DELETE FROM step_results WHERE run_id = ?", (rid,))
                    await db.execute_insert("DELETE FROM generated_configs WHERE run_id = ?", (rid,))
                    await db.execute_insert("DELETE FROM hil_requests WHERE run_id = ?", (rid,))
                    await db.execute_insert("DELETE FROM pipeline_runs WHERE id = ?", (rid,))
                    if doc_set_id:
                        await db.execute_insert("DELETE FROM uc_document_sets WHERE id = ?", (doc_set_id,))
                # Remove row from table directly — no DOM rebuild needed
                self.table.rows = [r for r in self.table.rows if r.get("id") != rid]
                ui.notify(f"Deleted {uc}", type="positive")
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        # Confirm via simple notify with action
        with ui.dialog() as dialog:
            with ui.card().classes("p-4 cr-card-inner"):
                ui.label(f"Delete all artifacts for {uc}?").classes("text-body2 font-semibold")
                ui.label("This action cannot be undone.").classes("text-caption cr-text-error")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat color=grey-5 size=sm")

                    async def confirm():
                        dialog.close()
                        await do_delete()

                    ui.button("Delete", icon="delete", on_click=confirm) \
                        .props("unelevated color=red-6 size=sm")
        dialog.open()
