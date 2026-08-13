"""Reusable GUI widgets and threading helpers.

Tkinter is not thread-safe, so long-running DICOM operations run on a worker
thread and marshal their progress/results back to the UI thread via
``widget.after(0, ...)``.
"""

from __future__ import annotations

import threading
import traceback
from typing import Callable

import customtkinter as ctk

from ..model import Node
from .theme import MUTED, mono


PAD = 10


class LogBox(ctk.CTkTextbox):
    """A read-only, auto-scrolling text area for streaming progress."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.configure(state="disabled", wrap="word", font=mono(14))

    def write(self, message: str) -> None:
        self.configure(state="normal")
        self.insert("end", message + "\n")
        self.see("end")
        self.configure(state="disabled")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


def run_threaded(widget, work: Callable[[], object],
                 on_done: Callable[[object], None] | None = None,
                 on_error: Callable[[Exception], None] | None = None) -> None:
    """Run ``work()`` on a thread; deliver result/exception on the UI thread."""

    def runner():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            widget.after(0, lambda: (on_error or _default_error)(exc, tb))
            return
        if on_done:
            widget.after(0, lambda: on_done(result))

    threading.Thread(target=runner, daemon=True).start()


def _default_error(exc: Exception, tb: str) -> None:
    print(tb)


def ui_progress(widget, logbox: LogBox) -> Callable[[str], None]:
    """Return a progress callback safe to call from a worker thread."""

    def cb(message: str) -> None:
        widget.after(0, lambda: logbox.write(message))

    return cb


class DestinationPicker(ctk.CTkFrame):
    """Dropdown of saved destinations, resolving to a :class:`Node`."""

    def __init__(self, master, app, label: str = "Destination",
                 remember_key: str = "", **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app
        self.remember_key = remember_key
        self._nodes: list[Node] = []

        ctk.CTkLabel(self, text=label, width=90, anchor="w").grid(
            row=0, column=0, padx=(0, PAD), sticky="w")
        self.combo = ctk.CTkComboBox(self, values=[], width=380,
                                     command=self._on_select)
        self.combo.grid(row=0, column=1, sticky="ew")
        self.detail = ctk.CTkLabel(self, text="", text_color=MUTED,
                                   anchor="w")
        self.detail.grid(row=1, column=1, sticky="w", pady=(2, 0))
        self.grid_columnconfigure(1, weight=1)
        self.refresh()

    def _remembered(self) -> str:
        if self.remember_key:
            return self.app.settings.last_destinations.get(self.remember_key,
                                                           "")
        return ""

    def refresh(self) -> None:
        self._nodes = list(self.app.destinations)
        labels = [n.name for n in self._nodes]
        self.combo.configure(values=labels)
        if labels:
            current = self.combo.get()
            if current not in labels:
                # Prefer the tool's remembered destination, else the first.
                remembered = self._remembered()
                self.combo.set(remembered if remembered in labels
                               else labels[0])
            self._on_select(self.combo.get())
        else:
            self.combo.set("")
            self.detail.configure(text="No destinations - add some in Settings.")

    def _on_select(self, choice: str) -> None:
        node = self.get_node()
        if node:
            self.detail.configure(
                text=f"{node.aetitle} @ {node.host}:{node.port}")
            if self.remember_key:
                self.app.remember_destination(self.remember_key, node.name)

    def get_node(self) -> Node | None:
        name = self.combo.get()
        for n in self._nodes:
            if n.name == name:
                return n
        return None


def build_drop_zone(app, parent, on_paths, action_verb: str, button_specs):
    """A prominent drag-and-drop area with the tool's Add buttons inside it.

    ``button_specs`` is a list of ``(text, command, width)``. Returns
    ``(zone_frame, count_label)`` - the caller updates ``count_label``.
    Highlights on drag-over so it's obvious files/folders can be dropped.
    """
    default_border = ("gray60", "gray45")
    zone = ctk.CTkFrame(parent, border_width=2, border_color=default_border,
                        corner_radius=10)
    zone.grid_columnconfigure(0, weight=1)

    big = ctk.CTkLabel(zone, text="↓   Drag files or folders here",
                       font=ctk.CTkFont(size=16, weight="bold"))
    big.grid(row=0, column=0, pady=(12, 2))
    sub = ctk.CTkLabel(zone, text=f"then press {action_verb}"
                                  "  —  or use the buttons below",
                       text_color=MUTED)
    sub.grid(row=1, column=0, pady=(0, 8))

    btnrow = ctk.CTkFrame(zone, fg_color="transparent")
    btnrow.grid(row=2, column=0, pady=(0, 12))
    for text, cmd, width in button_specs:
        ctk.CTkButton(btnrow, text=text, command=cmd, width=width).pack(
            side="left", padx=6)
    count = ctk.CTkLabel(btnrow, text="No files.", text_color=MUTED)
    count.pack(side="left", padx=12)

    def enter():
        zone.configure(border_color="#4f9dde")
        big.configure(text="↓   Release to add files / folders")

    def leave():
        zone.configure(border_color=default_border)
        big.configure(text="↓   Drag files or folders here")

    ok = app.enable_drop(zone, on_paths, on_enter=enter, on_leave=leave)
    for w in (big, sub, btnrow):
        app.enable_drop(w, on_paths, on_enter=enter, on_leave=leave)
    if not ok:
        big.configure(text="Add files or a folder")
        sub.configure(text="(drag-and-drop unavailable on this system)")
    return zone, count


def section(master, text: str) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(master, text=text,
                       font=ctk.CTkFont(size=15, weight="bold"), anchor="w")
    return lbl


def labeled_entry(master, label: str, default: str = "",
                  width: int = 200) -> tuple[ctk.CTkLabel, ctk.CTkEntry]:
    lbl = ctk.CTkLabel(master, text=label, anchor="w")
    entry = ctk.CTkEntry(master, width=width)
    if default:
        entry.insert(0, default)
    return lbl, entry
