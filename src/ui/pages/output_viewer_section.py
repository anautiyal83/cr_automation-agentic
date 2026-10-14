"""Output document viewer — browse and view all pipeline artifacts (input + output)."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from nicegui import ui

from src.storage.artifact_store import ArtifactStore

SYNTAX_MAP = {
    ".yaml": "yaml", ".yml": "yaml", ".xml": "xml", ".json": "json",
    ".py": "python", ".html": "html", ".txt": "text", ".csv": "text",
    ".xlsx": "text", ".xls": "text", ".docx": "text", ".pdf": "text",
}

ICON_MAP = {
    ".yaml": "data_object", ".yml": "data_object", ".json": "data_object",
    ".xml": "code", ".py": "code", ".html": "language",
    ".txt": "description", ".csv": "table_chart",
    ".xlsx": "table_chart", ".xls": "table_chart",
    ".docx": "description", ".pdf": "picture_as_pdf",
}


class OutputViewerSection:
    def __init__(self, artifact_store: ArtifactStore | None = None, orchestrator=None):
        self.artifact_store = artifact_store
        self.orchestrator = orchestrator
        self._current_file: Path | None = None
        self._build_ui()
        # Auto-load runs on init
        if orchestrator:
            async def _safe_load():
                try:
                    await self._load_runs()
                except RuntimeError:
                    pass
            ui.timer(0.5, _safe_load, once=True)

    def _build_ui(self):
        self._all_runs: list[dict] = []

        with ui.column().classes("w-full gap-4"):
            # Filter row
            with ui.row().classes("w-full items-end gap-3"):
                self.node_filter = ui.select(
                    {"": "All Nodes"}, value="", label="Node Type",
                    on_change=self._apply_filters,
                ).classes("min-w-36").props("outlined dense")
                self.activity_filter = ui.select(
                    {"": "All Activities"}, value="", label="Activity",
                    on_change=self._apply_filters,
                ).classes("min-w-36").props("outlined dense")
                self.run_select = ui.select(
                    [], label="Select Pipeline Run",
                    on_change=self._on_run_selected,
                ).classes("flex-1").props("outlined dense")
                ui.button("Refresh", icon="refresh", on_click=self._load_runs) \
                    .props("flat color=deep-purple-6 size=sm")

            # File list area (hidden until a run is selected)
            with ui.column().classes("w-full gap-2") as self.viewer_row:
                self.viewer_row.set_visibility(False)
                self.file_list = ui.column().classes("w-full gap-0")

            # Empty state
            self.empty_state = ui.column().classes("w-full items-center py-8 gap-2")
            with self.empty_state:
                ui.icon("folder_open", size="48px").classes("cr-text-dim")
                ui.label("No pipeline runs yet").classes("text-body2 cr-text-dim")
                ui.label("Complete a pipeline run to browse output documents") \
                    .classes("text-caption cr-text-dim")

    async def _load_runs(self):
        try:
            return await self._do_load_runs()
        except RuntimeError:
            pass

    async def _do_load_runs(self):
        if not self.orchestrator:
            return
        try:
            self._all_runs = await self.orchestrator.list_runs()
        except Exception:
            self._all_runs = []

        if not self._all_runs:
            self.run_select.options = []
            self.run_select.update()
            self.viewer_row.set_visibility(False)
            self.empty_state.set_visibility(True)
            return

        # Extract nodes and cascading activities
        nodes = set()
        all_activities: dict[str, set] = {}
        for r in self._all_runs:
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

        self._apply_filters_sync()

    def _apply_filters_sync(self):
        node = self.node_filter.value or ""
        activity = self.activity_filter.value or ""

        filtered = self._all_runs
        if node:
            filtered = [r for r in filtered if r.get("uc_name", "").startswith(node + "_") or r.get("uc_name", "") == node]
        if activity:
            filtered = [r for r in filtered if r.get("uc_name", "").endswith("_" + activity)]

        options = {}
        for r in filtered:
            rid = r.get("id", "")
            uc = r.get("uc_name", "unknown")
            status = str(r.get("status", "")).replace("PipelineStatus.", "")
            started = str(r.get("started_at") or "")[:16]
            options[rid] = f"{uc} ({status}) - {started}"
        self.run_select.options = options
        self.run_select.value = None
        self.run_select.update()
        self.empty_state.set_visibility(len(options) == 0)

    async def _apply_filters(self, e=None):
        try:
            self._apply_filters_sync()
        except RuntimeError:
            pass

    async def _on_run_selected(self, e):
        run_id = self.run_select.value
        if not run_id or not self.artifact_store:
            return
        self.viewer_row.set_visibility(True)
        self.empty_state.set_visibility(False)
        self._load_file_tree(run_id)

    def _load_file_tree(self, run_id: str):
        self.file_list.clear()
        try:
            artifacts = self.artifact_store.list_artifacts(UUID(run_id))
        except Exception:
            artifacts = []
        files = [a for a in artifacts if a.is_file()]

        if not files:
            with self.file_list:
                ui.label("No files found").classes("text-caption cr-text-dim py-4")
            return

        # Group by parent directory
        run_root = self.artifact_store.base_dir / run_id
        groups: dict[str, list[Path]] = {}
        for f in files:
            try:
                rel = f.relative_to(run_root)
                group = str(rel.parts[0]) if len(rel.parts) > 1 else "Artifacts"
            except ValueError:
                group = "Artifacts"
            groups.setdefault(group, []).append(f)

        with self.file_list:
            for group_name in sorted(groups.keys()):
                group_files = groups[group_name]
                label = group_name.replace("_", " ").title()
                with ui.expansion(label, icon="folder") \
                        .classes("w-full").props("dense header-class='text-body2 font-semibold'"):
                    for fpath in sorted(group_files, key=lambda p: p.name):
                        self._file_row(fpath)

    def _file_row(self, fpath: Path):
        suffix = fpath.suffix.lower()
        icon = ICON_MAP.get(suffix, "insert_drive_file")
        size = fpath.stat().st_size if fpath.exists() else 0

        with ui.row().classes("w-full items-center gap-2 py-1 px-2").style(
            "border-bottom: 1px solid var(--cr-border)"
        ):
            ui.icon(icon, size="16px").classes("cr-text-accent")
            ui.label(fpath.name).classes("text-xs font-mono flex-1")
            ui.label(_format_size(size)).classes("text-caption cr-text-dim")
            ui.button("View", icon="visibility",
                      on_click=lambda _, p=fpath: self._view_dialog(p)) \
                .props("flat dense size=xs color=deep-purple-6 no-caps")
            ui.button("Download", icon="download",
                      on_click=lambda _, p=fpath: ui.download(str(p))) \
                .props("flat dense size=xs color=teal-6 no-caps")

    @staticmethod
    def _view_dialog(fpath: Path):
        import html as html_mod
        content = ""
        is_binary = fpath.suffix.lower() in (".xlsx", ".xls", ".pdf", ".zip", ".jar", ".docx")

        if not is_binary and fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = "(Unable to decode file as text)"

        dialog = ui.dialog().props("maximized")
        with dialog, ui.card().classes("w-full h-full p-0").style(
            "display: flex; flex-direction: column"
        ):
            with ui.row().classes("items-center justify-between w-full px-4 py-2").style(
                "border-bottom: 1px solid var(--cr-border); flex-shrink: 0"
            ):
                ui.label(fpath.name).classes("text-subtitle2 font-bold font-mono")
                with ui.row().classes("gap-1"):
                    ui.button("Download", icon="download",
                              on_click=lambda: ui.download(str(fpath))) \
                        .props("flat size=sm color=teal-6 no-caps")
                    ui.button(icon="close", on_click=dialog.close) \
                        .props("flat round size=sm color=grey-6")
            with ui.scroll_area().classes("flex-1"):
                if is_binary:
                    with ui.column().classes("items-center justify-center p-8 gap-2"):
                        ui.icon("description", size="48px").classes("cr-text-dim")
                        ui.label(f"Binary file ({_format_size(fpath.stat().st_size)})") \
                            .classes("text-body2 cr-text-dim")
                        ui.label("Use Download to view this file").classes("text-caption cr-text-dim")
                elif content:
                    escaped = html_mod.escape(content)
                    ui.html(
                        f'<pre style="margin:0; padding: 16px; font-size: 12px; '
                        f'line-height: 1.5; font-family: monospace">{escaped}</pre>'
                    )
                else:
                    ui.label("No content available").classes("p-4 cr-text-dim")
        dialog.open()

    async def refresh_for_run(self, run_id: UUID):
        """Called externally to show outputs for a specific completed run."""
        await self._load_runs()
        self.run_select.value = str(run_id)
        self.viewer_row.set_visibility(True)
        self.empty_state.set_visibility(False)
        self._load_file_tree(str(run_id))


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"
