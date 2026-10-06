"""Settings section — polished configuration UI."""
from __future__ import annotations

from nicegui import ui


class SettingsSection:
    def __init__(self, config_manager=None):
        self.config_manager = config_manager
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-5"):

            # --- LLM Configuration ---
            self._section_header("LLM Configuration", "smart_toy",
                                 "Configure OpenRouter API access and model selection")
            with ui.card().classes("w-full p-4").style(
                "background: #2A2A3E; border: 1px solid #3A3A50; border-radius: 12px"
            ):
                with ui.column().classes("w-full gap-3"):
                    with ui.row().classes("w-full items-end gap-3"):
                        self.api_key_input = ui.input(
                            "OpenRouter API Key",
                            placeholder="sk-or-...",
                            password=True, password_toggle_button=True,
                        ).classes("flex-1").props("outlined dense")
                        ui.button("Validate & Save", icon="vpn_key",
                                  on_click=self._save_key) \
                            .props("unelevated color=deep-purple-6 size=sm")

                    self.model_input = ui.input(
                        "Default Model",
                        value="openrouter/anthropic/claude-sonnet-4-20250514",
                    ).classes("w-full").props("outlined dense")

                    ui.label("Per-step model overrides (leave empty to use default)") \
                        .classes("text-caption text-[#9090A8] mt-2")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        self.step_models = {}
                        for i in range(1, 7):
                            self.step_models[i] = ui.input(
                                f"Step {i}", placeholder="default",
                            ).classes("flex-1 min-w-40").props("outlined dense")

            # --- Retry Configuration ---
            self._section_header("Retry Configuration", "replay",
                                 "Control retry behavior for test failures and infrastructure errors")
            with ui.card().classes("w-full p-4").style(
                "background: #2A2A3E; border: 1px solid #3A3A50; border-radius: 12px"
            ):
                with ui.row().classes("w-full gap-4"):
                    with ui.column().classes("flex-1 gap-1"):
                        ui.label("Test Failure Retries").classes("text-caption text-[#9090A8]")
                        self.test_retry = ui.slider(min=1, max=10, value=3, step=1) \
                            .props("label-always color=deep-purple-6")
                    with ui.column().classes("flex-1 gap-1"):
                        ui.label("Infrastructure Retries").classes("text-caption text-[#9090A8]")
                        self.infra_retry = ui.slider(min=1, max=10, value=3, step=1) \
                            .props("label-always color=cyan-6")
                    with ui.column().classes("flex-1 gap-1"):
                        ui.label("Backoff Multiplier").classes("text-caption text-[#9090A8]")
                        self.backoff = ui.number(value=2.0, min=1.0, max=10.0, step=0.5) \
                            .props("outlined dense").classes("w-full")

            # --- External Modules ---
            self._section_header("External Modules", "integration_instructions",
                                 "Paths to Java JAR files and simulator configuration")
            with ui.card().classes("w-full p-4").style(
                "background: #2A2A3E; border: 1px solid #3A3A50; border-radius: 12px"
            ):
                with ui.column().classes("w-full gap-3"):
                    with ui.row().classes("w-full gap-3"):
                        self.ciq_jar = ui.input(
                            "CIQ Processor JAR", value="./lib/ciq-processor.jar",
                        ).classes("flex-1").props("outlined dense")
                        self.cli_jar = ui.input(
                            "CLI Automation JAR", value="./lib/cli-automation-standalone.jar",
                        ).classes("flex-1").props("outlined dense")
                    self.sim_endpoint = ui.input(
                        "Mock Simulator Endpoint", value="localhost:8080",
                    ).classes("w-full").props("outlined dense")

            # Save all
            with ui.row().classes("w-full justify-end"):
                ui.button("Save All Settings", icon="save",
                          on_click=self._save_all) \
                    .props("unelevated color=deep-purple-6 size=md")

    @staticmethod
    def _section_header(title: str, icon: str, desc: str):
        with ui.row().classes("items-center gap-2 mt-2"):
            ui.icon(icon, size="20px").classes("text-[#6C63FF]")
            ui.label(title).classes("text-subtitle1 font-semibold")
        ui.label(desc).classes("text-caption text-[#9090A8] ml-7 -mt-1")

    async def _save_key(self):
        if self.config_manager and self.api_key_input.value:
            await self.config_manager.update_config({"openrouter_api_key": self.api_key_input.value})
        ui.notify("API key saved", type="positive")

    async def _save_all(self):
        if self.config_manager:
            await self.config_manager.update_config({
                "default_model": self.model_input.value,
                "test_retry_limit": int(self.test_retry.value),
                "infra_retry_limit": int(self.infra_retry.value),
                "infra_retry_backoff": float(self.backoff.value),
                "ciq_processor_jar": self.ciq_jar.value,
                "cli_automation_jar": self.cli_jar.value,
            })
        ui.notify("All settings saved", type="positive")
