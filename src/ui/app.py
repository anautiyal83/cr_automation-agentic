"""NiceGUI application entry point — polished single-page layout."""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# Suppress HuggingFace warnings — must be set before any HF imports
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*huggingface.*")
warnings.filterwarnings("ignore", message=".*unauthenticated.*")
import logging as _logging
_logging.getLogger("huggingface_hub").setLevel(_logging.ERROR)
_logging.getLogger("sentence_transformers").setLevel(_logging.ERROR)

from nicegui import ui, app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Load .env file if present
_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from src.core.pipeline import PipelineOrchestrator
from src.storage.artifact_store import ArtifactStore
from src.storage.db import Database, init_database
from src.ui.pages.upload_section import UploadSection
from src.ui.pages.progress_section import ProgressSection
from src.ui.pages.history_section import HistorySection
from src.ui.pages.cleanup_section import CleanupSection
from src.ui.pages.settings_section import SettingsSection
from src.ui.pages.hil_dialog import HILDialog
from src.ui.pages.output_viewer_section import OutputViewerSection

_db: Database | None = None
_orchestrator: PipelineOrchestrator | None = None
_artifact_store: ArtifactStore | None = None
_config_manager = None

CUSTOM_CSS = """
<style>
/* --- Dark theme (default) --- */
body.body--dark {
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
    --cr-header-bg: linear-gradient(90deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    --cr-body-bg: linear-gradient(135deg, #0F0F1A 0%, #1A1A2E 50%, #16213E 100%);
    --cr-hero-bg: linear-gradient(135deg, rgba(108,99,255,0.1) 0%, rgba(0,217,255,0.05) 100%);
    --cr-shadow: rgba(0,0,0,0.3);
    --cr-card-value: #E8E8F0;
    background: var(--cr-body-bg) !important;
}
/* --- Light theme --- */
body.body--light {
    --cr-primary: #4F46E5;
    --cr-primary-dark: #4338CA;
    --cr-accent: #0891B2;
    --cr-surface: #FFFFFF;
    --cr-surface-light: #F8FAFC;
    --cr-surface-hover: #F1F5F9;
    --cr-text: #1E293B;
    --cr-text-dim: #64748B;
    --cr-success: #16A34A;
    --cr-error: #DC2626;
    --cr-warning: #D97706;
    --cr-border: #E2E8F0;
    --cr-header-bg: linear-gradient(90deg, #1E293B 0%, #334155 50%, #1E293B 100%);
    --cr-body-bg: #F1F5F9;
    --cr-hero-bg: linear-gradient(135deg, rgba(79,70,229,0.06) 0%, rgba(8,145,178,0.03) 100%);
    --cr-shadow: rgba(0,0,0,0.08);
    --cr-card-value: #1E293B;
    background: var(--cr-body-bg) !important;
}
.cr-header {
    background: var(--cr-header-bg) !important;
    border-bottom: 1px solid var(--cr-border);
    box-shadow: 0 2px 12px var(--cr-shadow);
    z-index: 1000;
}
.cr-header .q-btn {
    z-index: 1001;
    pointer-events: auto !important;
}
.cr-hero {
    background: var(--cr-hero-bg);
    border: 1px solid var(--cr-border);
    border-radius: 10px;
    padding: 14px 18px;
}
.cr-stat-card {
    background: var(--cr-surface) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 12px !important;
    transition: all 0.2s ease;
    width: 90px !important;
    height: 90px !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    text-align: center;
}
.cr-stat-card .q-card__section {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 12px !important;
}
.cr-stat-card:hover {
    border-color: var(--cr-primary) !important;
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(108,99,255,0.15);
}
.cr-stat-icon {
    font-size: 20px !important;
    margin-bottom: 4px;
}
.cr-stat-value {
    color: var(--cr-card-value) !important;
}
.cr-stat-title {
    color: var(--cr-text-dim) !important;
}
.cr-section {
    background: var(--cr-surface) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 8px !important;
    overflow: hidden;
}
.cr-section .q-expansion-item__toggle-icon {
    color: var(--cr-accent) !important;
}
.cr-upload-card {
    background: var(--cr-surface-light) !important;
    border: 2px dashed var(--cr-border) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease;
    padding: 10px;
    min-height: 150px;
    display: flex !important;
    flex-direction: column !important;
}
.cr-upload-card .q-card__section {
    flex: 1;
    display: flex;
    flex-direction: column;
}
.cr-upload-card:hover {
    border-color: var(--cr-primary) !important;
    background: var(--cr-surface-hover) !important;
}
.cr-upload-card.uploaded {
    border-color: var(--cr-success) !important;
    border-style: solid !important;
}
@keyframes cr-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.cr-spin .material-icons, .cr-spin { animation: cr-spin 1.5s linear infinite; }
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
.cr-badge-required { background: rgba(248,113,113,0.15); color: var(--cr-error); }
.cr-badge-optional { background: rgba(144,144,168,0.15); color: var(--cr-text-dim); }
.q-expansion-item {
    border-radius: 12px !important;
    overflow: hidden;
    margin-bottom: 4px;
}
/* --- Utility classes for theme-aware colors --- */
.cr-text-primary { color: var(--cr-text) !important; }
.cr-text-dim { color: var(--cr-text-dim) !important; }
.cr-text-accent { color: var(--cr-primary) !important; }
.cr-text-success { color: var(--cr-success) !important; }
.cr-text-error { color: var(--cr-error) !important; }
.cr-text-warning { color: var(--cr-warning) !important; }
.cr-bg-surface { background: var(--cr-surface) !important; }
.cr-bg-surface-light { background: var(--cr-surface-light) !important; }
.cr-border { border-color: var(--cr-border) !important; }
.cr-step-circle {
    width: 36px; height: 36px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--cr-surface-light); border: 2px solid var(--cr-border);
    transition: all 0.3s ease;
}
.cr-step-circle-active { background: rgba(108,99,255,0.15); }
.cr-step-connector {
    flex-shrink: 0; display: flex; align-items: center; margin-top: 16px;
}
.cr-step-connector::before {
    content: ''; display: block; width: 16px; height: 2px;
    background: var(--cr-border); transition: background 0.3s;
}
.cr-step-connector::after {
    content: ''; display: block; width: 0; height: 0;
    border-top: 5px solid transparent; border-bottom: 5px solid transparent;
    border-left: 6px solid var(--cr-border); transition: border-color 0.3s;
}
.cr-step-row-connector {
    display: flex; align-items: center; justify-content: center;
    padding: 2px 0; color: var(--cr-border);
}
.cr-step-row-connector svg { width: 20px; height: 20px; }
.cr-card-inner {
    background: var(--cr-surface-light) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 8px;
}
.cr-settings-tab {
    border: 1px solid var(--cr-border) !important;
    border-radius: 6px !important;
    color: var(--cr-text-dim) !important;
    font-size: 11px !important;
    padding: 2px 8px !important;
    min-height: 28px !important;
    transition: all 0.15s ease;
}
.cr-settings-tab:hover {
    background: var(--cr-surface-hover) !important;
    color: var(--cr-text) !important;
}
.cr-settings-tab.active {
    background: var(--cr-primary) !important;
    color: white !important;
    border-color: var(--cr-primary) !important;
}
/* Layout */
html, body { margin: 0 !important; height: 100vh; overflow: hidden; }
* { box-sizing: border-box; }
.nicegui-content { padding: 48px 0 0 0 !important; height: 100vh; overflow: hidden; }
.q-page-container { padding-top: 48px !important; height: calc(100vh - 48px); overflow: hidden; }
.q-page { min-height: 0 !important; padding: 0 !important; height: 100%; }
/* --- Sidebar --- */
.cr-sidebar {
    background: var(--cr-surface) !important;
    border-right: 1px solid var(--cr-border);
    width: 160px;
    min-width: 160px;
    height: calc(100vh - 48px);
    max-height: calc(100vh - 48px);
    position: fixed;
    top: 48px;
    left: 0;
    overflow-y: auto;
    overflow-x: hidden;
    z-index: 100;
    padding: 10px 0;
    display: flex;
    flex-direction: column;
}
.cr-sidebar::-webkit-scrollbar { width: 4px; }
.cr-sidebar::-webkit-scrollbar-thumb { background: var(--cr-border); border-radius: 2px; }
.cr-sidebar::-webkit-scrollbar-track { background: transparent; }
.cr-sidebar-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    cursor: pointer;
    transition: all 0.15s ease;
    color: var(--cr-text-dim);
    font-size: 11px;
    font-weight: 500;
    border-left: 3px solid transparent;
    user-select: none;
}
.cr-sidebar-item:hover {
    background: var(--cr-surface-hover);
    color: var(--cr-text);
}
.cr-sidebar-item.active {
    background: var(--cr-surface-hover);
    color: var(--cr-primary);
    border-left-color: var(--cr-primary);
}
.cr-sidebar-item .material-icons {
    font-size: 16px;
}
.cr-sidebar-divider {
    height: 1px;
    background: var(--cr-border);
    margin: 4px 12px;
}
.cr-content {
    margin-left: 160px;
    padding: 6px 10px;
    height: calc(100vh - 48px);
    overflow-y: auto;
    overflow-x: hidden;
}
.cr-content::-webkit-scrollbar { width: 6px; }
.cr-content::-webkit-scrollbar-thumb { background: var(--cr-border); border-radius: 3px; }
.cr-content::-webkit-scrollbar-track { background: transparent; }
/* Let section cards fill width and not constrain height */
.cr-section { overflow: visible !important; width: 100% !important; }
.cr-section > .q-card__section { overflow: visible !important; width: 100% !important; }
/* Tables fill width */
.cr-content .q-table { width: 100% !important; }
.cr-content .q-table__container { width: 100% !important; }
</style>
"""


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"


async def _load_config(db):
    """Load pipeline config from YAML file + DB settings overlay."""
    global _config_manager
    from src.core.config import ConfigManager
    mgr = ConfigManager(db_accessor=None)
    config = await mgr.get_config()

    # Overlay DB-saved settings (highest priority)
    try:
        rows = await db.execute("SELECT key, value FROM settings", ())
        db_settings = {r["key"]: r["value"] for r in rows}
        updates = {}
        if db_settings.get("api_key"):
            updates["openrouter_api_key"] = db_settings["api_key"]
        if db_settings.get("default_model"):
            updates["default_model"] = db_settings["default_model"]
        if db_settings.get("test_retry_limit"):
            updates["test_retry_limit"] = int(db_settings["test_retry_limit"])
        if db_settings.get("infra_retry_limit"):
            updates["infra_retry_limit"] = int(db_settings["infra_retry_limit"])
        if db_settings.get("backoff_multiplier"):
            updates["infra_retry_backoff"] = float(db_settings["backoff_multiplier"])
        if db_settings.get("ciq_processor_jar"):
            updates["ciq_processor_jar"] = db_settings["ciq_processor_jar"]
        if db_settings.get("cli_automation_jar"):
            updates["cli_automation_jar"] = db_settings["cli_automation_jar"]
        if db_settings.get("execution_report_jar"):
            updates["execution_report_jar"] = db_settings["execution_report_jar"]
        if db_settings.get("mop_template"):
            updates["mop_template"] = db_settings["mop_template"]
        if db_settings.get("exec_summary_template"):
            updates["exec_summary_template"] = db_settings["exec_summary_template"]
        if db_settings.get("exec_failure_template"):
            updates["exec_failure_template"] = db_settings["exec_failure_template"]
        if db_settings.get("step_models"):
            import json
            try:
                sm = json.loads(db_settings["step_models"])
                if sm:
                    updates["step_models"] = {int(k): v for k, v in sm.items()}
            except Exception:
                pass
        if updates:
            config = await mgr.update_config(updates)
    except Exception:
        pass

    # Also set API key in environment for liteLLM
    if config.openrouter_api_key:
        os.environ["OPENROUTER_API_KEY"] = config.openrouter_api_key

    _config_manager = mgr
    return config


async def _ensure_initialized():
    global _db, _orchestrator, _artifact_store
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = await init_database(str(DATA_DIR / "pipeline.db"))
        _artifact_store = ArtifactStore(str(DATA_DIR / "runs"))
        config = await _load_config(_db)
        _orchestrator = PipelineOrchestrator(_db, _artifact_store, config=config, config_manager=_config_manager)


def create_app() -> None:

    @ui.page("/")
    async def main_page():
        await _ensure_initialized()

        ui.add_head_html(CUSTOM_CSS)
        ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">')
        # Hide disconnect overlay and auto-reconnect
        ui.add_head_html("""<style>
.nicegui-reconnecting { display: none !important; }
.nicegui-disconnected { opacity: 0.3; pointer-events: none; }
</style>""")
        ui.add_head_html("""<style>
body { font-family: "Inter", sans-serif !important; font-size: 12px !important; }
.q-field--dense .q-field__control { min-height: 28px !important; }
.q-field--dense .q-field__label { font-size: 11px !important; }
.q-field--dense .q-field__native, .q-field--dense input { font-size: 12px !important; }
.q-btn--dense { min-height: 28px !important; font-size: 11px !important; }
.q-card { padding: 8px !important; }
.q-card__section { padding: 6px 10px !important; }
.text-h5 { font-size: 16px !important; line-height: 1.3 !important; }
.text-h6 { font-size: 14px !important; line-height: 1.3 !important; }
.text-subtitle1 { font-size: 13px !important; }
.text-subtitle2 { font-size: 12px !important; }
.text-body1 { font-size: 12px !important; }
.text-body2 { font-size: 11px !important; }
.text-caption { font-size: 10px !important; }
.q-expansion-item__content { padding: 4px 8px !important; }
.q-slider { margin: 2px 0 !important; }
.q-badge { font-size: 10px !important; padding: 1px 6px !important; }
.gap-5 { gap: 10px !important; }
.gap-4 { gap: 8px !important; }
.gap-3 { gap: 6px !important; }
.gap-2 { gap: 4px !important; }
.p-4 { padding: 10px !important; }
.p-5 { padding: 12px !important; }
.p-3 { padding: 8px !important; }
.py-2 { padding-top: 4px !important; padding-bottom: 4px !important; }
.mb-2 { margin-bottom: 4px !important; }
.mt-2 { margin-top: 4px !important; }
</style>""")

        dark = ui.dark_mode(False)
        is_dark = {"value": False}

        # --- Header ---
        with ui.header(fixed=True).classes("cr-header items-center justify-between px-4").style("min-height: 48px; height: 48px"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("precision_manufacturing", size="20px").classes("text-[#6C63FF]")
                ui.label("CR Automation").classes("text-body2 font-bold leading-tight")
                ui.label("·").style("color: #9090A8; font-size: 10px")
                ui.label("Agentic Pipeline").classes("text-caption leading-tight").style("color: #9090A8")
            with ui.row().classes("items-center gap-2"):
                ui.badge("v0.1.0").props("color=grey-8 text-color=grey-4 outline")
                theme_btn = ui.button(icon="light_mode") \
                    .props("flat round size=sm color=grey-5") \
                    .tooltip("Toggle theme")

                def toggle_theme():
                    is_dark["value"] = not is_dark["value"]
                    dark.set_value(is_dark["value"])
                    theme_btn.props(f'icon={"dark_mode" if is_dark["value"] else "light_mode"}')

                theme_btn.on_click(toggle_theme)

        hil_dialog = HILDialog()
        progress = None

        # --- Menu items definition ---
        menu_items = [
            ("dashboard", "Dashboard", "dashboard"),
            ("new_run", "New Pipeline Run", "add_circle_outline"),
            ("progress", "Pipeline Progress", "view_timeline"),
            ("outputs", "Output Documents", "source"),
            ("history", "Run History", "history"),
            ("cleanup", "Artifact Cleanup", "cleaning_services"),
            ("settings", "Settings", "tune"),
        ]

        # Content containers dict — filled below
        content_panels: dict = {}
        sidebar_items: dict = {}
        active_section = {"value": "dashboard"}
        on_navigate_hooks: dict[str, list] = {}  # section -> [async callbacks]

        def navigate(section: str):
            prev = active_section["value"]
            active_section["value"] = section
            # Toggle visibility
            for key, panel in content_panels.items():
                panel.set_visibility(key == section)
            # Update sidebar active state
            if prev in sidebar_items:
                sidebar_items[prev].classes(remove="active")
            if section in sidebar_items:
                sidebar_items[section].classes(add="active")
            # Fire refresh hooks for the target section
            for hook in on_navigate_hooks.get(section, []):
                try:
                    import asyncio
                    async def _safe_hook(fn=hook):
                        try:
                            await fn()
                        except RuntimeError:
                            pass
                    asyncio.ensure_future(_safe_hook())
                except Exception:
                    pass

        async def on_start_pipeline(doc_set, review_milestones=True):
            try:
                run = await _orchestrator.start_run(doc_set, review_milestones=review_milestones)
                if progress:
                    await progress.start_tracking(run.id)
                navigate("progress")
                ui.notify(f"Pipeline started for {doc_set.uc_name}", type="positive")
            except Exception as exc:
                navigate("progress")
                ui.notify(f"Pipeline start failed: {exc}", type="negative")

        # --- Sidebar ---
        with ui.column().classes("cr-sidebar"):
            # Brand area
            with ui.column().classes("px-5 pb-3 gap-0"):
                ui.label("NAVIGATION").classes("text-[10px] font-bold tracking-widest cr-stat-title")
            ui.element("div").classes("cr-sidebar-divider")

            for key, label, icon in menu_items:
                item = ui.element("div").classes(
                    f"cr-sidebar-item {'active' if key == 'dashboard' else ''}"
                )
                with item:
                    ui.icon(icon)
                    ui.label(label)
                item.on("click", lambda _, k=key: navigate(k))
                sidebar_items[key] = item

            # Spacer + footer
            ui.element("div").style("flex:1")
            ui.element("div").classes("cr-sidebar-divider")
            with ui.column().classes("px-5 py-3 gap-0"):
                ui.label("CR Automation v0.1.0").classes("text-[11px] cr-stat-title")

        # --- Content area ---
        with ui.column().classes("cr-content"):

            # Dashboard
            with ui.column().classes("w-full gap-5") as panel:
                content_panels["dashboard"] = panel
                with ui.element("div").classes("cr-hero"):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-1"):
                            ui.label("Use Case Automation Pipeline") \
                                .classes("text-h5 font-bold cr-text-primary")
                            ui.label(
                                "Upload telecom UC documents, generate YAML configs via LLM agents, "
                                "validate and test automatically"
                            ).classes("text-body2 cr-text-dim max-w-xl")
                        with ui.row().classes("gap-3"):
                            runs_label = _stat_card("Pipeline Runs", "0", "rocket_launch", "#6C63FF")
                            success_label = _stat_card("Success Rate", "—", "check_circle", "#4ADE80")
                            duration_label = _stat_card("Avg Duration", "—", "timer", "#00D9FF")

                async def refresh_dashboard():
                    try:
                        rows = await _db.execute(
                            "SELECT status, duration_seconds FROM pipeline_runs", ()
                        )
                        total = len(rows)
                        completed = sum(1 for r in rows if 'completed' in str(r.get('status', '')).lower())
                        durations = [r['duration_seconds'] for r in rows
                                     if r.get('duration_seconds') and 'completed' in str(r.get('status', '')).lower()]
                        runs_label.text = str(total)
                        if total > 0:
                            success_label.text = f"{completed * 100 // total}%"
                        else:
                            success_label.text = "—"
                        if durations:
                            avg = sum(durations) / len(durations)
                            duration_label.text = f"{avg:.0f}s"
                        else:
                            duration_label.text = "—"
                    except Exception:
                        pass

                on_navigate_hooks.setdefault("dashboard", []).append(refresh_dashboard)
                ui.timer(0.5, refresh_dashboard, once=True)

            # New Pipeline Run
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["new_run"] = panel
                panel.set_visibility(False)
                ui.label("New Pipeline Run").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    UploadSection(on_start_pipeline=on_start_pipeline)

            # Pipeline Progress
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["progress"] = panel
                panel.set_visibility(False)
                ui.label("Pipeline Progress").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    async def _progress_view_details(run_id):
                        navigate("history")
                        # Trigger details for this run in history section
                        if hasattr(_refs, 'history') and _refs.history:
                            await _refs.history._on_details(type('E', (), {'args': {'id': run_id}})())

                    async def _progress_view_outputs(run_id):
                        navigate("outputs")
                        if hasattr(_refs, 'output_viewer') and _refs.output_viewer:
                            from uuid import UUID as _UUID
                            await _refs.output_viewer.refresh_for_run(_UUID(run_id))

                    progress = ProgressSection(
                        orchestrator=_orchestrator,
                        on_view_details=_progress_view_details,
                        on_view_outputs=_progress_view_outputs,
                    )

            # Forward refs container
            class _refs:
                history = None
                output_viewer = None

            # Output Documents
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["outputs"] = panel
                panel.set_visibility(False)
                ui.label("Output Documents").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    output_viewer = OutputViewerSection(
                        artifact_store=_artifact_store, orchestrator=_orchestrator,
                    )
                    _refs.output_viewer = output_viewer
                    on_navigate_hooks.setdefault("outputs", []).append(output_viewer._load_runs)

            # Run History
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["history"] = panel
                panel.set_visibility(False)
                ui.label("Run History").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    async def on_rerun(run):
                        if progress:
                            await progress.start_tracking(run.id)
                        navigate("progress")

                    async def on_view_outputs(run_id):
                        navigate("outputs")
                        if output_viewer:
                            from uuid import UUID as _UUID
                            await output_viewer.refresh_for_run(_UUID(run_id))

                    history_section = HistorySection(
                        orchestrator=_orchestrator,
                        on_rerun=on_rerun,
                        on_view_outputs=on_view_outputs,
                    )
                    _refs.history = history_section
                    on_navigate_hooks.setdefault("history", []).append(history_section._refresh)

            # Artifact Cleanup
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["cleanup"] = panel
                panel.set_visibility(False)
                ui.label("Artifact Cleanup").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    cleanup_section = CleanupSection(orchestrator=_orchestrator, artifact_store=_artifact_store)
                    on_navigate_hooks.setdefault("cleanup", []).append(cleanup_section._refresh)

            # Settings
            with ui.column().classes("w-full gap-2") as panel:
                content_panels["settings"] = panel
                panel.set_visibility(False)
                ui.label("Settings").classes("text-h6 font-bold cr-stat-value")
                with ui.card().classes("cr-section w-full p-4"):
                    settings_section = SettingsSection(config_manager=_config_manager, db=_db)

            # Load saved settings
            await settings_section.load_saved_settings()


def _stat_card(title: str, value: str, icon: str, color: str) -> ui.label:
    """Create a stat card and return the value label for dynamic updates."""
    with ui.card().classes("cr-stat-card"):
        with ui.column().classes("items-center gap-0"):
            ui.icon(icon).classes("cr-stat-icon").style(f"color: {color}")
            val_label = ui.label(value).classes("text-h6 font-bold cr-stat-value")
            ui.label(title).classes("text-caption cr-stat-title")
    return val_label


def run() -> None:
    create_app()
    ui.run(
        host="0.0.0.0",
        port=8000,
        title="CR Automation Agentic",
        dark=False,
        reload=False,
        favicon="🔧",
        reconnect_timeout=60,
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
