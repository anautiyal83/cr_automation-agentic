"""Document upload and validation UI — polished card-based layout."""
from __future__ import annotations

import uuid
from pathlib import Path

from nicegui import events, ui

from src.core.document_validator import validate_documents
from src.core.models import UCDocumentSet

UPLOAD_DIR = Path("./data/uploads")

DOC_TYPES = [
    {"key": "ciq", "label": "CIQ Excel", "icon": "table_chart", "accept": ".xlsx,.xls",
     "desc": "Customer Installation Questionnaire", "required": True},
    {"key": "mop", "label": "MOP Document", "icon": "description", "accept": ".docx,.pdf,.txt",
     "desc": "Method of Procedure", "required": True},
    {"key": "constraints", "label": "Constraints", "icon": "rule", "accept": ".txt,.yaml,.yml,.json,.csv",
     "desc": "Validation rules & business rules", "required": False},
    {"key": "cmd_outputs", "label": "Command Outputs", "icon": "terminal", "accept": ".txt",
     "desc": "CLI/API session logs", "required": True},
]


class UploadSection:
    def __init__(self, on_start_pipeline=None) -> None:
        self.on_start_pipeline = on_start_pipeline
        self.uc_name = ""
        self.paths: dict[str, str | None] = {d["key"]: None for d in DOC_TYPES}
        self.statuses: dict[str, ui.label] = {}
        self.cards: dict[str, ui.element] = {}
        self.is_valid = False
        self._build_ui()

    def _build_ui(self) -> None:
        with ui.column().classes("w-full gap-5"):
            # UC Name input — prominent
            with ui.card().classes("w-full p-4").style(
                "background: rgba(108,99,255,0.06); border: 1px solid #3A3A50; border-radius: 12px"
            ):
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.icon("badge", size="24px").classes("text-[#6C63FF]")
                    self.name_input = ui.input(
                        "Use Case Name",
                        placeholder="e.g., VoLTE_Provisioning_SiteA",
                        validation={"Required": lambda v: bool(v and v.strip())},
                    ).classes("flex-1").props("outlined dense")
                    self.name_input.on("update:model-value", self._on_name_change)

            # Document upload grid
            with ui.row().classes("w-full gap-4 flex-wrap"):
                for doc in DOC_TYPES:
                    self._upload_card(doc)

            # Validation results
            self.validation_container = ui.column().classes("w-full")

            # Action row
            with ui.row().classes("w-full justify-between items-center"):
                self.validation_label = ui.label("Upload all required documents to continue") \
                    .classes("text-body2 text-[#9090A8]")
                self.start_btn = ui.button(
                    "Start Pipeline", icon="play_arrow",
                    on_click=self._on_start,
                ).props("unelevated color=deep-purple-6 size=lg rounded") \
                    .classes("px-8")
                self.start_btn.disable()

    def _upload_card(self, doc: dict) -> None:
        key = doc["key"]
        with ui.card().classes("cr-upload-card flex-1 min-w-56") as card:
            self.cards[key] = card
            with ui.column().classes("w-full gap-2 items-center"):
                # Header
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon(doc["icon"], size="20px").classes("text-[#6C63FF]")
                        ui.label(doc["label"]).classes("text-subtitle2 font-semibold")
                    badge_cls = "cr-badge cr-badge-required" if doc["required"] else "cr-badge cr-badge-optional"
                    ui.html(f'<span class="{badge_cls}">{"Required" if doc["required"] else "Optional"}</span>')

                ui.label(doc["desc"]).classes("text-caption text-[#9090A8] w-full")

                # Upload widget
                ui.upload(
                    label=f"Drop or click to upload",
                    auto_upload=True,
                    on_upload=lambda e, k=key: self._handle_upload(e, k),
                ).props(f'accept="{doc["accept"]}" flat bordered color=grey-8') \
                    .classes("w-full")

                # Status
                status = ui.label("").classes("text-xs")
                self.statuses[key] = status

    def _on_name_change(self, e) -> None:
        self.uc_name = e.args if isinstance(e.args, str) else (e.args or "")

    async def _handle_upload(self, e: events.UploadEventArguments, doc_key: str) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        uid = str(uuid.uuid4())[:8]
        dest = UPLOAD_DIR / f"{uid}_{e.name}"
        dest.write_bytes(e.content.read())

        self.paths[doc_key] = str(dest)
        self.statuses[doc_key].text = f"{e.name}"
        self.statuses[doc_key].classes(replace="text-xs text-[#4ADE80] font-medium")

        # Update card visual
        card = self.cards.get(doc_key)
        if card:
            card.classes(add="uploaded")

        await self._validate()

    async def _validate(self) -> None:
        self.validation_container.clear()

        if not (self.uc_name or "").strip():
            self.start_btn.disable()
            self.validation_label.text = "Enter a Use Case name to continue"
            return

        required_ready = all(self.paths[d["key"]] for d in DOC_TYPES if d["required"])
        if not required_ready:
            self.start_btn.disable()
            missing = [d["label"] for d in DOC_TYPES if d["required"] and not self.paths[d["key"]]]
            self.validation_label.text = f"Missing: {', '.join(missing)}"
            return

        result = await validate_documents(
            uc_name=self.uc_name,
            ciq_path=self.paths["ciq"],
            mop_path=self.paths["mop"],
            constraints_path=self.paths["constraints"],
            command_outputs_path=self.paths["cmd_outputs"],
        )

        self.is_valid = result.valid

        if result.valid:
            self.start_btn.enable()
            self.validation_label.text = ""
            with self.validation_container:
                with ui.row().classes("items-center gap-2 p-3").style(
                    "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.2); border-radius: 8px"
                ):
                    ui.icon("check_circle", size="20px").classes("text-[#4ADE80]")
                    ui.label("All documents validated successfully — ready to start pipeline") \
                        .classes("text-body2 text-[#4ADE80]")
        else:
            self.start_btn.disable()
            self.validation_label.text = ""
            with self.validation_container:
                with ui.column().classes("gap-1 p-3").style(
                    "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.2); border-radius: 8px"
                ):
                    for err in result.errors:
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("error_outline", size="16px").classes("text-[#F87171]")
                            ui.label(f"{err.document}: {err.message}").classes("text-body2 text-[#F87171]")

    async def _on_start(self) -> None:
        if not self.is_valid or not self.on_start_pipeline:
            return
        doc_set = UCDocumentSet(
            uc_name=self.uc_name.strip(),
            ciq_file_path=self.paths["ciq"],
            mop_file_path=self.paths["mop"],
            constraints_file_path=self.paths["constraints"],
            command_outputs_path=self.paths["cmd_outputs"],
            validated=True,
        )
        self.start_btn.disable()
        self.start_btn.props("loading")
        await self.on_start_pipeline(doc_set)
