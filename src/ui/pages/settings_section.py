"""Settings section — polished configuration UI with DB persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from nicegui import ui

AVAILABLE_MODELS = [
    # Tier 1: Best value (paid)
    {"id": "openrouter/google/gemini-2.5-flash", "label": "Gemini 2.5 Flash", "tier": "Best Value", "cost": "$0.15/$0.60"},
    {"id": "openrouter/deepseek/deepseek-chat", "label": "DeepSeek V3", "tier": "Best Value", "cost": "$0.14/$0.28"},
    {"id": "openrouter/google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite", "tier": "Best Value", "cost": "$0.075/$0.30"},
    # Tier 2: Premium
    {"id": "openrouter/anthropic/claude-sonnet-4", "label": "Claude Sonnet 4", "tier": "Premium", "cost": "$3/$15"},
    {"id": "openrouter/google/gemini-2.5-pro", "label": "Gemini 2.5 Pro", "tier": "Premium", "cost": "$1.25/$10"},
    {"id": "openrouter/openai/gpt-4o", "label": "GPT-4o", "tier": "Premium", "cost": "$2.50/$10"},
    # Tier 3: Free (rate limited)
    {"id": "openrouter/google/gemini-2.5-flash:free", "label": "Gemini 2.5 Flash (Free)", "tier": "Free", "cost": "Free"},
    {"id": "openrouter/meta-llama/llama-3.3-70b-instruct:free", "label": "Llama 3.3 70B (Free)", "tier": "Free", "cost": "Free"},
    {"id": "openrouter/deepseek/deepseek-chat:free", "label": "DeepSeek V3 (Free)", "tier": "Free", "cost": "Free"},
]

MODEL_OPTIONS = {m["id"]: f'{m["label"]}  —  {m["cost"]}' for m in AVAILABLE_MODELS}


class SettingsSection:
    def __init__(self, config_manager=None, db=None):
        self.config_manager = config_manager
        self.db = db
        self._build_ui()

    async def _load_setting(self, key: str, default: str = "") -> str:
        if not self.db:
            return default
        try:
            rows = await self.db.query("settings", where="key = ?", params=(key,))
            if rows:
                return rows[0].get("value", default)
        except Exception:
            pass
        return default

    async def _save_setting(self, key: str, value: str) -> None:
        if not self.db:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.db.execute_insert(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
        except Exception:
            pass

    async def load_saved_settings(self) -> None:
        """Load persisted settings into the UI widgets."""
        val = await self._load_setting("default_model")
        if val:
            self.model_select.value = val

        val = await self._load_setting("api_key")
        if val:
            self.api_key_input.value = val

        val = await self._load_setting("test_retry_limit")
        if val:
            self.test_retry.value = int(val)

        val = await self._load_setting("infra_retry_limit")
        if val:
            self.infra_retry.value = int(val)

        val = await self._load_setting("backoff_multiplier")
        if val:
            self.backoff.value = float(val)

        val = await self._load_setting("ciq_processor_jar")
        if val:
            self.ciq_jar.value = val

        val = await self._load_setting("cli_automation_jar")
        if val:
            self.cli_jar.value = val

        val = await self._load_setting("simulator_endpoint")
        if val:
            self.sim_endpoint.value = val

        val = await self._load_setting("simulator_folder")
        if val:
            self.sim_folder.value = val

        val = await self._load_setting("step_models")
        if val:
            try:
                step_data = json.loads(val)
                for k, v in step_data.items():
                    step_num = int(k)
                    if step_num in self.step_models and v:
                        self.step_models[step_num].value = v
            except Exception:
                pass

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):

            # Tab navigation
            settings_tabs = [
                ("api", "API & Models", "smart_toy"),
                ("step_models", "Per-Step Models", "tune"),
                ("retry", "Retry Policy", "replay"),
                ("modules", "External Modules", "integration_instructions"),
            ]
            tab_panels: dict[str, ui.column] = {}
            tab_btns: dict[str, ui.element] = {}
            active_tab = {"value": "api"}

            def switch_tab(key: str):
                prev = active_tab["value"]
                active_tab["value"] = key
                for k, p in tab_panels.items():
                    p.set_visibility(k == key)
                if prev in tab_btns:
                    tab_btns[prev].classes(remove="active")
                if key in tab_btns:
                    tab_btns[key].classes(add="active")

            # Tab bar
            with ui.row().classes("w-full gap-2 flex-wrap"):
                for key, label, icon in settings_tabs:
                    btn = ui.button(label, icon=icon, on_click=lambda _, k=key: switch_tab(k)) \
                        .props("flat no-caps size=sm") \
                        .classes("cr-settings-tab" + (" active" if key == "api" else ""))
                    tab_btns[key] = btn

            # ── Tab 1: API & Models ──
            with ui.column().classes("w-full gap-4") as panel:
                tab_panels["api"] = panel
                self._card_header("OpenRouter API Key", "vpn_key",
                                  "Required for LLM-powered pipeline steps (1-6)")
                with ui.card().classes("w-full p-5 cr-card-inner"):
                    with ui.row().classes("w-full items-end gap-3"):
                        self.api_key_input = ui.input(
                            "API Key",
                            placeholder="sk-or-...",
                            password=True, password_toggle_button=True,
                        ).classes("flex-1").props("outlined dense")
                        ui.button("Save Key", icon="vpn_key",
                                  on_click=self._save_key) \
                            .props("unelevated color=deep-purple-6 size=sm")

                self._card_header("Default LLM Model", "model_training",
                                  "Used for all steps unless overridden per-step")
                with ui.card().classes("w-full p-5 cr-card-inner"):
                    self.model_select = ui.select(
                        MODEL_OPTIONS,
                        value="openrouter/google/gemini-2.5-flash",
                        label="Default Model",
                    ).classes("w-full").props("outlined dense")

            # ── Tab 2: Per-Step Models ──
            with ui.column().classes("w-full gap-4") as panel:
                tab_panels["step_models"] = panel
                panel.set_visibility(False)
                self._card_header("Per-Step Model Overrides", "tune",
                                  "Assign different models to individual pipeline steps. Empty = use default.")

                step_names = {
                    1: "Step 1: Document Parser",
                    2: "Step 2: UC Analyzer",
                    3: "Step 3: Validation Rules Gen",
                    4: "Step 4: JSON Output Gen",
                    6: "Step 6: Workflow Gen",
                    7: "Step 7: Simulator Config Gen",
                }
                step_options = {"": "Use default", **MODEL_OPTIONS}
                self.step_models = {}
                for i in step_names:
                    with ui.card().classes("w-full p-4 cr-card-inner"):
                        with ui.row().classes("items-center gap-3 w-full"):
                            ui.icon("smart_toy", size="18px").classes("cr-text-accent")
                            ui.label(step_names[i]).classes("text-body2 font-semibold min-w-48")
                            self.step_models[i] = ui.select(
                                step_options,
                                value="",
                                label="Model",
                            ).classes("flex-1").props("outlined dense")

            # ── Tab 3: Retry Policy ──
            with ui.column().classes("w-full gap-4") as panel:
                tab_panels["retry"] = panel
                panel.set_visibility(False)
                self._card_header("Retry Configuration", "replay",
                                  "Control retry behavior for test failures and infrastructure errors")

                with ui.row().classes("w-full gap-4"):
                    with ui.card().classes("flex-1 p-5 cr-card-inner"):
                        with ui.column().classes("gap-2"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("bug_report", size="18px").classes("cr-text-accent")
                                ui.label("Test Failure Retries").classes("text-body2 font-semibold")
                            ui.label("Retries when generated config fails validation tests") \
                                .classes("text-caption cr-text-dim")
                            self.test_retry = ui.slider(min=1, max=10, value=3, step=1) \
                                .props("label-always color=deep-purple-6")

                    with ui.card().classes("flex-1 p-5 cr-card-inner"):
                        with ui.column().classes("gap-2"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("cloud_off", size="18px").classes("cr-text-accent")
                                ui.label("Infrastructure Retries").classes("text-body2 font-semibold")
                            ui.label("Retries for LLM API timeouts and network errors") \
                                .classes("text-caption cr-text-dim")
                            self.infra_retry = ui.slider(min=1, max=10, value=3, step=1) \
                                .props("label-always color=cyan-6")

                    with ui.card().classes("flex-1 p-5 cr-card-inner"):
                        with ui.column().classes("gap-2"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("timer", size="18px").classes("cr-text-accent")
                                ui.label("Backoff Multiplier").classes("text-body2 font-semibold")
                            ui.label("Exponential backoff factor between retries") \
                                .classes("text-caption cr-text-dim")
                            self.backoff = ui.number(value=2.0, min=1.0, max=10.0, step=0.5) \
                                .props("outlined dense").classes("w-full")

            # ── Tab 4: External Modules ──
            with ui.column().classes("w-full gap-4") as panel:
                tab_panels["modules"] = panel
                panel.set_visibility(False)
                self._card_header("Java Modules", "integration_instructions",
                                  "Paths to cr-automation Java JAR files")
                with ui.card().classes("w-full p-5 cr-card-inner"):
                    with ui.column().classes("w-full gap-3"):
                        self.ciq_jar = ui.input(
                            "CIQ Processor JAR", value="./lib/ciq-processor.jar",
                        ).classes("w-full").props("outlined dense")
                        self.cli_jar = ui.input(
                            "CLI Automation JAR", value="./lib/cli-automation-standalone.jar",
                        ).classes("w-full").props("outlined dense")

                self._card_header("Mock Simulator", "dns",
                                  "Simulator endpoint and folder for mock configs")
                with ui.card().classes("w-full p-5 cr-card-inner"):
                    with ui.column().classes("w-full gap-3"):
                        self.sim_endpoint = ui.input(
                            "Simulator Endpoint", value="localhost:8080",
                        ).classes("w-full").props("outlined dense")
                        self.sim_folder = ui.input(
                            "Simulator Folder", value="./lib/simulator/",
                        ).classes("w-full").props("outlined dense")

            # Save all button — always visible
            with ui.row().classes("w-full justify-end pt-2"):
                ui.button("Save All Settings", icon="save",
                          on_click=self._save_all) \
                    .props("unelevated color=deep-purple-6 size=md")

    @staticmethod
    def _card_header(title: str, icon: str, desc: str):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="20px").classes("cr-text-accent")
            ui.label(title).classes("text-subtitle2 font-semibold")
        ui.label(desc).classes("text-caption cr-text-dim ml-7 -mt-1 mb-1")

    async def _save_key(self):
        await self._save_setting("api_key", self.api_key_input.value or "")
        if self.config_manager and self.api_key_input.value:
            await self.config_manager.update_config({"openrouter_api_key": self.api_key_input.value})
        ui.notify("API key saved", type="positive")

    async def _save_all(self):
        await self._save_setting("default_model", self.model_select.value or "")
        await self._save_setting("test_retry_limit", str(int(self.test_retry.value)))
        await self._save_setting("infra_retry_limit", str(int(self.infra_retry.value)))
        await self._save_setting("backoff_multiplier", str(float(self.backoff.value)))
        await self._save_setting("ciq_processor_jar", self.ciq_jar.value or "")
        await self._save_setting("cli_automation_jar", self.cli_jar.value or "")
        await self._save_setting("simulator_endpoint", self.sim_endpoint.value or "")
        await self._save_setting("simulator_folder", self.sim_folder.value or "")
        await self._save_setting("api_key", self.api_key_input.value or "")

        step_data = {}
        for i, widget in self.step_models.items():
            if widget.value:
                step_data[str(i)] = widget.value
        await self._save_setting("step_models", json.dumps(step_data))

        if self.config_manager:
            await self.config_manager.update_config({
                "default_model": self.model_select.value,
                "openrouter_api_key": self.api_key_input.value,
                "test_retry_limit": int(self.test_retry.value),
                "infra_retry_limit": int(self.infra_retry.value),
                "infra_retry_backoff": float(self.backoff.value),
                "ciq_processor_jar": self.ciq_jar.value,
                "cli_automation_jar": self.cli_jar.value,
            })
        ui.notify("All settings saved", type="positive")
