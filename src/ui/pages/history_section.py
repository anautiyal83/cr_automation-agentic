"""Run history section — filterable table with re-run, view, and detail support."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from nicegui import ui


class HistorySection:
    def __init__(self, orchestrator=None, on_rerun=None, on_view_outputs=None):
        self.orchestrator = orchestrator
        self.on_rerun = on_rerun  # callback(run) → navigate to progress
        self.on_view_outputs = on_view_outputs  # callback(run_id) → navigate to outputs
        self._build_ui()
        # Auto-load on init
        if orchestrator:
            ui.timer(0.5, self._safe_refresh, once=True)

    async def _safe_refresh(self):
        try:
            await self._refresh()
        except RuntimeError:
            pass

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Filter bar
            with ui.card().classes("w-full p-3 cr-card-inner"):
                with ui.row().classes("w-full items-end gap-3"):
                    self.node_filter = ui.select(
                        {"": "All Nodes"}, value="", label="Node Type",
                        on_change=lambda _: self._refresh(),
                    ).props("outlined dense").classes("min-w-32")
                    self.activity_filter = ui.select(
                        {"": "All Activities"}, value="", label="Activity",
                        on_change=lambda _: self._refresh(),
                    ).props("outlined dense").classes("min-w-32")
                    self.status_filter = ui.select(
                        {"all": "All Statuses", "completed": "Completed",
                         "failed": "Failed", "cancelled": "Cancelled", "running": "Running"},
                        value="all", label="Status",
                        on_change=lambda _: self._refresh(),
                    ).props("outlined dense").classes("min-w-32")

            # Results table
            self.table = ui.table(
                columns=[
                    {"name": "uc_name", "label": "UC Name", "field": "uc_name",
                     "sortable": True, "align": "left"},
                    {"name": "status", "label": "Status", "field": "status",
                     "sortable": True, "align": "center"},
                    {"name": "started_at", "label": "Started", "field": "started_at",
                     "sortable": True, "align": "left"},
                    {"name": "duration", "label": "Duration", "field": "duration_seconds",
                     "align": "right"},
                    {"name": "error", "label": "Error", "field": "has_error",
                     "align": "center"},
                    {"name": "actions", "label": "Actions", "field": "actions",
                     "align": "center"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full").props(
                'flat bordered separator=cell dense '
                'table-header-class="cr-bg-surface-light cr-text-dim" '
                'no-data-label="No pipeline runs found"'
            )
            # Custom body slot for error column — show clickable "Details" link
            self.table.add_slot('body-cell-error', '''
                <q-td :props="props">
                    <q-btn v-if="props.row.error_summary" flat dense no-caps
                           icon="error_outline" color="red-6" size="sm"
                           label="Details"
                           @click="$parent.$emit('show_error', props.row)" />
                    <span v-else class="text-grey-5">—</span>
                </q-td>
            ''')
            # Custom body slot for action buttons
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense icon="info_outline" color="grey-7" size="sm"
                           @click="$parent.$emit('details', props.row)"
                           label="Details" no-caps class="q-mr-xs" />
                    <q-btn flat dense icon="source" color="teal-6" size="sm"
                           @click="$parent.$emit('view', props.row)"
                           label="Outputs" no-caps class="q-mr-xs" />
                    <q-btn flat dense icon="replay" color="deep-purple-6" size="sm"
                           @click="$parent.$emit('rerun', props.row)"
                           label="Re-run" no-caps />
                </q-td>
            ''')
            self.table.on('show_error', self._on_show_error)
            self.table.on('rerun', self._on_rerun)
            self.table.on('view', self._on_view)
            self.table.on('details', self._on_details)

            # Detail panel (shown below table when a run is selected)
            self.detail_panel = ui.card().classes("w-full p-4 cr-card-inner")
            self.detail_panel.set_visibility(False)
            self.detail_content = ui.column().classes("w-full gap-3")
            with self.detail_panel:
                with ui.row().classes("w-full items-center justify-between"):
                    self.detail_title = ui.label("").classes("text-subtitle2 font-bold")
                    ui.button(icon="close", on_click=lambda: self.detail_panel.set_visibility(False)) \
                        .props("flat round size=xs color=grey-6")
                # Content populated dynamically
                self.detail_content = ui.column().classes("w-full gap-3")

            self.empty_state = ui.column().classes("w-full items-center py-6 gap-2")
            with self.empty_state:
                ui.icon("inbox", size="48px").classes("cr-text-dim")
                ui.label("No runs yet").classes("text-body2 cr-text-dim")
                ui.label("Complete a pipeline run to see it here") \
                    .classes("text-caption cr-text-dim")

    async def _refresh(self):
        try:
            return await self._do_refresh()
        except RuntimeError:
            pass

    async def _do_refresh(self):
        if not self.orchestrator:
            return
        filters = {}
        if self.status_filter.value != "all":
            filters["status"] = self.status_filter.value
        try:
            runs = await self.orchestrator.list_runs(filters)
        except Exception:
            runs = []

        # Extract all nodes and activities from runs
        nodes = set()
        all_activities: dict[str, set] = {}  # node -> {activities}
        for r in runs:
            uc = r.get("uc_name", "")
            parts = uc.split("_", 1)
            if len(parts) == 2:
                nodes.add(parts[0])
                all_activities.setdefault(parts[0], set()).add(parts[1])
                all_activities.setdefault("", set()).add(parts[1])  # "" = all nodes
            elif uc:
                nodes.add(uc)

        # Populate node dropdown (preserve current selection)
        node_opts = {"": "All Nodes"}
        node_opts.update({n: n for n in sorted(nodes)})
        self.node_filter.options = node_opts
        self.node_filter.update()

        # Populate activity dropdown based on selected node
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

        # Format rows for display
        for r in runs:
            if r.get("started_at"):
                r["started_at"] = str(r["started_at"])[:19]
            dur = r.get("duration_seconds")
            r["duration_seconds"] = f"{dur:.1f}s" if dur else "—"
            r["status"] = str(r.get("status", "")).replace("PipelineStatus.", "")
            r["has_error"] = bool(r.get("error_summary"))
            r["error_summary"] = r.get("error_summary") or ""
        self.table.rows = runs
        self.empty_state.set_visibility(len(runs) == 0)

    async def _on_details(self, e):
        row = e.args
        run_id = row.get("id")
        if not run_id or not self.orchestrator:
            return

        self.detail_title.text = f"Run Details — {row.get('uc_name', '')} ({row.get('status', '')})"
        self.detail_content.clear()
        self.detail_panel.set_visibility(True)

        db = self.orchestrator.db

        with self.detail_content:
            # --- Input Documents ---
            doc_set_id = None
            try:
                run_row = await db.get_by_id("pipeline_runs", run_id)
                doc_set_id = run_row.get("document_set_id") if run_row else None
            except Exception:
                pass

            if doc_set_id:
                try:
                    doc = await db.get_by_id("uc_document_sets", str(doc_set_id))
                except Exception:
                    doc = None
                if doc:
                    with ui.expansion("Input Documents", icon="upload_file") \
                            .classes("w-full").props("dense default-opened"):
                        with ui.column().classes("gap-1"):
                            for key, label in [
                                ("ciq_file_path", "CIQ Excel"),
                                ("mop_file_path", "MOP Document"),
                                ("sample_io_xml_paths", "Sample I/O XMLs"),
                                ("constraints_file_path", "Constraints"),
                                ("command_outputs_path", "Command Outputs"),
                                ("additional_documents", "Additional Documents"),
                            ]:
                                val = doc.get(key)
                                if not val or val == "null":
                                    continue
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon("insert_drive_file", size="14px").classes("cr-text-accent")
                                    ui.label(f"{label}:").classes("text-caption font-semibold min-w-32")
                                    ui.label(str(val)[:100]).classes("text-caption cr-text-dim font-mono")

            # --- Step Results ---
            try:
                steps = await db.execute(
                    "SELECT step_number, step_name, status, started_at, completed_at, "
                    "error_message, retry_count FROM step_results WHERE run_id = ? "
                    "ORDER BY step_number",
                    (run_id,),
                )
            except Exception:
                steps = []

            if steps:
                with ui.expansion("Pipeline Steps", icon="view_timeline") \
                        .classes("w-full").props("dense default-opened"):
                    for step in steps:
                        status = step.get("status", "pending")
                        color = {
                            "StepStatus.PASSED": "positive", "passed": "positive",
                            "StepStatus.FAILED": "negative", "failed": "negative",
                            "StepStatus.IN_PROGRESS": "info", "in_progress": "info",
                        }.get(status, "grey")
                        with ui.row().classes("items-center gap-2 w-full py-1"):
                            ui.badge(f"Step {step['step_number']}").props(f"color={color}")
                            ui.label(step.get("step_name", "")).classes("text-caption font-semibold flex-1")
                            ui.label(status.replace("StepStatus.", "")).classes("text-caption cr-text-dim")
                            retries = step.get("retry_count", 0)
                            if retries:
                                ui.badge(f"{retries} retries").props("color=warning outline")
                        err = step.get("error_message")
                        if err:
                            ui.label(f"  Error: {err[:120]}").classes("text-caption cr-text-error ml-8")

            # --- Output Artifacts ---
            try:
                artifacts = await db.execute(
                    "SELECT config_type, file_path, schema_valid, generated_by_step "
                    "FROM generated_configs WHERE run_id = ? ORDER BY generated_by_step",
                    (run_id,),
                )
            except Exception:
                artifacts = []

            if artifacts:
                with ui.expansion("Output Artifacts", icon="folder_open") \
                        .classes("w-full").props("dense default-opened"):
                    for art in artifacts:
                        fpath = art.get("file_path", "")
                        exists = Path(fpath).exists() if fpath else False
                        with ui.row().classes("items-center gap-2 w-full py-1"):
                            ui.icon(
                                "check_circle" if exists else "error_outline",
                                size="14px",
                            ).classes("cr-text-success" if exists else "cr-text-error")
                            ui.label(art.get("config_type", "")).classes("text-caption font-semibold min-w-32")
                            ui.label(Path(fpath).name if fpath else "—").classes("text-caption cr-text-dim font-mono")
                            valid = art.get("schema_valid")
                            if valid is not None:
                                ui.badge("valid" if valid else "invalid").props(
                                    f"color={'positive' if valid else 'negative'} outline"
                                )

            # --- Error Summary ---
            err_summary = row.get("error_summary")
            if err_summary:
                with ui.expansion("Error Summary", icon="error_outline") \
                        .classes("w-full").props("dense"):
                    ui.label(err_summary).classes("text-caption cr-text-error font-mono")

    async def _on_show_error(self, e):
        """Show full error details in a popup dialog."""
        import html as html_mod
        row = e.args
        uc_name = row.get("uc_name", "Unknown")
        error = row.get("error_summary", "No error details available.")
        status = row.get("status", "")

        dialog = ui.dialog().props("medium")
        with dialog, ui.card().classes("w-full p-0").style("min-width: 500px; max-width: 700px"):
            # Header
            with ui.row().classes("items-center justify-between w-full px-5 py-3").style(
                "background: #fdf2f2; border-bottom: 1px solid #fce4e4"
            ):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("error_outline", size="22px").style("color: #e74c3c")
                    ui.label(f"Error — {uc_name}").classes("text-subtitle2 font-bold")
                ui.button(icon="close", on_click=dialog.close) \
                    .props("flat round size=sm color=grey-6")
            # Body
            with ui.column().classes("w-full px-5 py-4 gap-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Status:").classes("text-caption font-semibold")
                    ui.badge(status).props("color=negative")
                ui.separator()
                ui.label("Error Details").classes("text-caption font-semibold")
                ui.html(
                    f'<pre style="margin:0; padding: 12px; font-size: 12px; line-height: 1.6; '
                    f'font-family: monospace; background: #fef9f9; border-radius: 6px; '
                    f'color: #c0392b; white-space: pre-wrap; word-break: break-word;">'
                    f'{html_mod.escape(error)}</pre>'
                )
        dialog.open()

    async def _on_view(self, e):
        row = e.args
        run_id = row.get("id")
        if run_id and self.on_view_outputs:
            await self.on_view_outputs(run_id)

    async def _on_rerun(self, e):
        row = e.args
        run_id = row.get("id")
        if not run_id or not self.orchestrator:
            return
        try:
            new_run = await self.orchestrator.rerun(UUID(run_id))
            ui.notify(f"Re-running {row.get('uc_name', 'UC')}...", type="positive")
            if self.on_rerun:
                await self.on_rerun(new_run)
        except Exception as exc:
            ui.notify(f"Re-run failed: {exc}", type="negative")
