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
