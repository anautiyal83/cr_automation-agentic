"""Theme management — dark/light mode per FR-041."""
from __future__ import annotations

from nicegui import ui


def setup_theme() -> None:
    ui.dark_mode(True)


def create_theme_toggle():
    dark = ui.dark_mode()
    dark.value = True
    return ui.button(icon="dark_mode", on_click=dark.toggle).props("flat round color=white")
