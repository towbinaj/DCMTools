"""Base class for tool panels."""

from __future__ import annotations

import customtkinter as ctk

from .theme import MUTED, tool_color
from .widgets import LogBox, ui_progress, PAD


class ToolPanel(ctk.CTkFrame):
    """A single tool's UI: header, a body for controls, and a log area."""

    title = "Tool"
    description = ""

    def __init__(self, master, app, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        accent = tool_color(type(self).__name__)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        titlerow = ctk.CTkFrame(header, fg_color="transparent")
        titlerow.pack(anchor="w", fill="x")
        # colored accent bar next to the title
        ctk.CTkFrame(titlerow, width=6, height=28, corner_radius=3,
                     fg_color=accent).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(titlerow, text=self.title, text_color=accent,
                     font=ctk.CTkFont(size=22, weight="bold"),
                     anchor="w").pack(side="left")
        if self.description:
            ctk.CTkLabel(header, text=self.description, text_color=MUTED,
                         anchor="w", justify="left").pack(anchor="w")

        self.body = ctk.CTkFrame(self)
        self.body.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.body.grid_columnconfigure(0, weight=1)

        self.log = LogBox(self)
        self.log.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(0, PAD))
        self.progress = ui_progress(self, self.log)

        self.build()

    # Subclasses override these -------------------------------------------
    def build(self) -> None:
        """Populate ``self.body`` with controls."""

    def on_destinations_changed(self) -> None:
        """Called when the destinations registry changes."""

    def on_show(self) -> None:
        """Called each time the panel becomes visible."""
