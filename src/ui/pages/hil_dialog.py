"""Human-in-the-Loop dialog per FR-029."""
from __future__ import annotations
from uuid import UUID
from nicegui import ui
from src.core.hil import hil_handler


class HILDialog:
    def __init__(self):
        self.dialog = None

    async def show(self, hil_data: dict):
        """Show HIL dialog for user input."""
        with ui.dialog() as self.dialog, ui.card().classes("w-96"):
            ui.label("Input Required").classes("text-h6")

            requests = hil_data.get("hil_requests", [hil_data]) if hil_data.get("batch") else [hil_data]
            responses = {}

            for req in requests:
                hil_id = req.get("hil_id", "")
                ui.separator()
                ui.label(req.get("field", "")).classes("font-bold")
                ui.label(f"Agent inferred: {req.get('inference', '')}").classes("text-sm text-grey")
                ui.label(req.get("question", "")).classes("text-sm")

                options = req.get("options")
                if options:
                    select = ui.select(options, label="Choose an option").classes("w-full")
                    responses[hil_id] = select
                else:
                    inp = ui.input("Your answer").classes("w-full")
                    responses[hil_id] = inp

            async def submit():
                for hil_id, widget in responses.items():
                    value = widget.value or ""
                    if value:
                        await hil_handler.respond(UUID(hil_id), str(value))
                self.dialog.close()

            ui.button("Submit", on_click=submit).props("color=positive")

        self.dialog.open()
