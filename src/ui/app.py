"""NiceGUI application entry point — polished single-page layout."""
from __future__ import annotations

import sys
from pathlib import Path

from nicegui import ui, app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.core.pipeline import PipelineOrchestrator
from src.storage.artifact_store import ArtifactStore
from src.storage.db import Database, init_database
from src.ui.pages.upload_section import UploadSection
from src.ui.pages.progress_section import ProgressSection
from src.ui.pages.history_section import HistorySection
from src.ui.pages.cleanup_section import CleanupSection
from src.ui.pages.settings_section import SettingsSection
from src.ui.pages.hil_dialog import HILDialog

_db: Database | None = None
_orchestrator: PipelineOrchestrator | None = None
_artifact_store: ArtifactStore | None = None

CUSTOM_CSS = """
<style>
:root {
    --cr-primary: #6C63FF;
    --cr-primary-dark: #5A52D5;
    --cr-accent: #00D9FF;
    --cr-surface: #1E1E2E;
    --cr-surface-light: #2A2A3E;
    --cr-surface-hover: #33334A;
    --cr-text: #E8E8F0;
    --cr-text-dim: #9090A8;
    --cr-success: #4ADE80;
    --cr-error: #F87171;
    --cr-warning: #FBBF24;
    --cr-border: #3A3A50;
}
body.body--dark {
    background: linear-gradient(135deg, #0F0F1A 0%, #1A1A2E 50%, #16213E 100%) !important;
}
.cr-header {
    background: linear-gradient(90deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%) !important;
    border-bottom: 1px solid var(--cr-border);
    box-shadow: 0 2px 12px rgba(0,0,0,0.3);
}
.cr-hero {
    background: linear-gradient(135deg, rgba(108,99,255,0.1) 0%, rgba(0,217,255,0.05) 100%);
    border: 1px solid var(--cr-border);
    border-radius: 16px;
    padding: 32px;
}
.cr-stat-card {
    background: var(--cr-surface) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 12px !important;
    transition: all 0.2s ease;
}
.cr-stat-card:hover {
    border-color: var(--cr-primary) !important;
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(108,99,255,0.15);
}
.cr-section {
    background: var(--cr-surface) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 12px !important;
}
.cr-section .q-expansion-item__toggle-icon {
    color: var(--cr-accent) !important;
}
.cr-upload-card {
    background: var(--cr-surface-light) !important;
    border: 2px dashed var(--cr-border) !important;
    border-radius: 12px !important;
    transition: all 0.2s ease;
    padding: 16px;
}
.cr-upload-card:hover {
    border-color: var(--cr-primary) !important;
    background: var(--cr-surface-hover) !important;
}
.cr-upload-card.uploaded {
    border-color: var(--cr-success) !important;
    border-style: solid !important;
}
.cr-step-chip {
    border-radius: 8px !important;
    min-width: 90px;
    text-align: center;
}
.cr-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.cr-badge-required { background: rgba(248,113,113,0.15); color: #F87171; }
.cr-badge-optional { background: rgba(144,144,168,0.15); color: #9090A8; }
.q-expansion-item {
    border-radius: 12px !important;
    overflow: hidden;
    margin-bottom: 4px;
}
.nicegui-content { padding-top: 72px !important; }
</style>
"""


async def _ensure_initialized():
    global _db, _orchestrator, _artifact_store
    if _db is None:
        _db = await init_database("./data/pipeline.db")
        _artifact_store = ArtifactStore("./data/runs")
        _orchestrator = PipelineOrchestrator(_db, _artifact_store)


def create_app() -> None:

    @ui.page("/")
    async def main_page():
        await _ensure_initialized()

        ui.add_head_html(CUSTOM_CSS)
        ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">')
        ui.add_head_html('<style>body { font-family: "Inter", sans-serif !important; }</style>')

        dark = ui.dark_mode(True)

        # --- Header ---
        with ui.header(fixed=True).classes("cr-header items-center justify-between px-6"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("precision_manufacturing", size="28px").classes("text-[#6C63FF]")
                with ui.column().classes("gap-0"):
                    ui.label("CR Automation").classes("text-subtitle1 font-bold leading-tight")
                    ui.label("Agentic Pipeline").classes("text-caption text-[#9090A8] leading-tight")
            with ui.row().classes("items-center gap-2"):
                ui.badge("v0.1.0").props("color=grey-8 text-color=grey-4 outline")
                ui.button(icon="dark_mode", on_click=dark.toggle) \
                    .props("flat round size=sm color=grey-5") \
                    .tooltip("Toggle theme")

        hil_dialog = HILDialog()
        progress = None

        async def on_start_pipeline(doc_set):
            run = await _orchestrator.start_run(doc_set)
            if progress:
                await progress.start_tracking(run.id)
            ui.notify(f"Pipeline started for {doc_set.uc_name}", type="positive")

        with ui.column().classes("w-full max-w-7xl mx-auto px-6 py-6 gap-5"):

            # --- Hero section ---
            with ui.element("div").classes("cr-hero"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("gap-1"):
                        ui.label("Use Case Automation Pipeline") \
                            .classes("text-h5 font-bold text-white")
                        ui.label(
                            "Upload telecom UC documents, generate YAML configs via LLM agents, "
                            "validate and test automatically"
                        ).classes("text-body2 text-[#9090A8] max-w-xl")
                    with ui.row().classes("gap-3"):
                        _stat_card("Pipeline Runs", "0", "rocket_launch", "#6C63FF")
                        _stat_card("Success Rate", "—", "check_circle", "#4ADE80")
                        _stat_card("Avg Duration", "—", "timer", "#00D9FF")

            # --- Upload section ---
            with ui.expansion("New Pipeline Run", icon="add_circle_outline") \
                    .classes("cr-section w-full").props("default-opened header-class='text-subtitle1 font-semibold'"):
                UploadSection(on_start_pipeline=on_start_pipeline)

            # --- Pipeline progress ---
            with ui.expansion("Pipeline Progress", icon="view_timeline") \
                    .classes("cr-section w-full").props("header-class='text-subtitle1 font-semibold'"):
                progress = ProgressSection(orchestrator=_orchestrator)

            # --- Run history ---
            with ui.expansion("Run History", icon="history") \
                    .classes("cr-section w-full").props("header-class='text-subtitle1 font-semibold'"):
                HistorySection(orchestrator=_orchestrator)

            # --- Cleanup ---
            with ui.expansion("Artifact Cleanup", icon="cleaning_services") \
                    .classes("cr-section w-full").props("header-class='text-subtitle1 font-semibold'"):
                CleanupSection(orchestrator=_orchestrator, artifact_store=_artifact_store)

            # --- Settings ---
            with ui.expansion("Settings", icon="tune") \
                    .classes("cr-section w-full").props("header-class='text-subtitle1 font-semibold'"):
                SettingsSection()

            # --- Footer ---
            with ui.row().classes("w-full justify-center py-4"):
                ui.label("CR Automation Agentic Framework") \
                    .classes("text-caption text-[#9090A8]")


def _stat_card(title: str, value: str, icon: str, color: str):
    with ui.card().classes("cr-stat-card p-4 min-w-32"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="20px").style(f"color: {color}")
            ui.label(title).classes("text-caption text-[#9090A8]")
        ui.label(value).classes("text-h6 font-bold mt-1")


def run() -> None:
    create_app()
    ui.run(
        host="0.0.0.0",
        port=8000,
        title="CR Automation Agentic",
        dark=True,
        reload=False,
        favicon="precision_manufacturing",
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
