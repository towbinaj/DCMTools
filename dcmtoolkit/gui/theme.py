"""Shared visual theme: readable colors, fonts, and UI scaling.

Centralizes the tweaks that make the app easier to read: a higher-contrast
"muted" text color (instead of a flat mid-gray that washes out in both light and
dark), a slightly larger monospace font for log areas, and a user-adjustable
global widget scale.
"""

from __future__ import annotations

import customtkinter as ctk

# Secondary/hint text. Tuple = (light-mode, dark-mode). Darker-on-light and
# lighter-on-dark than a flat "gray", so it stays legible in both themes.
MUTED = ("gray30", "gray74")

# Named UI scales offered in Settings -> Text size.
SCALE_OPTIONS: dict[str, float] = {
    "Normal": 1.0,
    "Large": 1.15,
    "Larger": 1.3,
    "Largest": 1.5,
}


def scale_name(value: float) -> str:
    for name, v in SCALE_OPTIONS.items():
        if abs(v - value) < 0.001:
            return name
    return "Large"


def apply_scale(value: float) -> None:
    """Scale every widget + font uniformly (CustomTkinter global setting)."""
    ctk.set_widget_scaling(value)


def mono(size: int = 13) -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size)


# Per-tool accent colors. Mid-tone hexes chosen to stay legible on both light
# and dark backgrounds. Used for the sidebar accent bar + panel title.
TOOL_COLORS: dict[str, str] = {
    "EchoPanel": "#4f9dde",          # blue
    "EchoAllPanel": "#3fb0a3",       # teal
    "SendPanel": "#e0894a",          # orange
    "QueryMovePanel": "#b57edc",     # purple
    "RetrievePanel": "#5cb85c",      # green
    "StorageCommitPanel": "#d9695f", # coral
    "TagListPanel": "#6fa8dc",       # light blue
    "ModifyPanel": "#d4a017",        # gold
    "SplitPanel": "#8e79c9",         # violet
    "DumpPanel": "#4a9ca0",          # dark teal
    "DeidentifyPanel": "#cf6f9b",    # pink
    "QAPanel": "#5cb85c",            # green
    "ModalitySCUPanel": "#e0a94a",   # amber-gold
    "PatientEditPanel": "#5aa9c9",   # cyan
    "StudyEditPanel": "#b08cd8",     # lavender
    "SeriesEditPanel": "#d69f5b",    # amber
    "ReceiverPanel": "#7fa650",      # olive
    "LogPanel": "#9aa0a6",           # gray
    "SettingsPanel": "#8a8f94",      # gray
}

DEFAULT_ACCENT = "#5b9bd5"


def tool_color(key: str) -> str:
    return TOOL_COLORS.get(key, DEFAULT_ACCENT)
