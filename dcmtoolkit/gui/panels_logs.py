"""Log viewer panel: tail the rotating activity log."""

from __future__ import annotations

import os
from pathlib import Path

import customtkinter as ctk

from .. import paths
from .base import ToolPanel
from .theme import MUTED
from .widgets import PAD


class LogPanel(ToolPanel):
    title = "Logs"
    description = "Live view of the application activity log."

    def build(self) -> None:
        self._auto = False
        self._logfile = paths.log_dir() / "dcmtoolkit.log"

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Refresh", width=80,
                      command=self.refresh).pack(side="left")
        self.auto_chk = ctk.CTkCheckBox(bar, text="Auto-refresh (2s)",
                                        command=self._toggle_auto)
        self.auto_chk.pack(side="left", padx=PAD)
        ctk.CTkButton(bar, text="Open log folder", width=120,
                      command=self._open_folder).pack(side="left")
        self.path_lbl = ctk.CTkLabel(bar, text=str(self._logfile),
                                     text_color=MUTED)
        self.path_lbl.pack(side="left", padx=PAD)

        self.body.grid_columnconfigure(0, weight=1)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.log.clear()
        try:
            # Show the tail (last ~400 lines) to stay responsive.
            lines = self._logfile.read_text(encoding="utf-8",
                                            errors="replace").splitlines()
            for line in lines[-400:]:
                self.log.write(line)
        except FileNotFoundError:
            self.log.write("(no log file yet)")
        except OSError as exc:
            self.log.write(f"(could not read log: {exc})")

    def _toggle_auto(self) -> None:
        self._auto = bool(self.auto_chk.get())
        if self._auto:
            self._tick()

    def _tick(self) -> None:
        if not self._auto:
            return
        self.refresh()
        self.after(2000, self._tick)

    def _open_folder(self) -> None:
        try:
            os.startfile(str(self._logfile.parent))  # noqa: S606 (Windows)
        except Exception:  # noqa: BLE001
            self.log.write(f"Log folder: {self._logfile.parent}")
