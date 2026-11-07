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

from ..model import Node, DEST_GROUP_STORAGE
from ..logging_setup import get_logger
from .theme import MUTED, mono


PAD = 10


class LogBox(ctk.CTkTextbox):
    """A read-only, auto-scrolling text area for streaming progress.

    Everything written here is also mirrored to the central rotating log file
    (tagged with the tool name), so every tool leaves a persistent, timestamped
    audit trail even though the on-screen box clears.
    """

    def __init__(self, master, logger_name: str = "", **kw):
        super().__init__(master, **kw)
        self.configure(state="disabled", wrap="word", font=mono(14))
        self._logger = get_logger(logger_name) if logger_name else None

    def write(self, message: str) -> None:
        self.configure(state="normal")
        self.insert("end", message + "\n")
        self.see("end")
        self.configure(state="disabled")
        if self._logger is not None:
            for line in str(message).splitlines() or [""]:
                if line.strip():
                    self._logger.info(line)

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")

    def set_text(self, text: str) -> None:
        """Replace the whole contents WITHOUT mirroring to the central log file.

        Used for views that are re-rendered as the user types (e.g. the Tag
        Lister's live filter) and that may contain tag values (PHI) which must
        not be written to the persistent log.
        """
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("end", text)
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
            # Bind exc/tb as defaults: `exc` is unbound once the except block
            # ends, so a bare closure would NameError when the lambda runs.
            widget.after(0, lambda e=exc, t=tb:
                         (on_error or _default_error)(e, t))
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
                 remember_key: str = "", groups=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app
        self.remember_key = remember_key
        # Which destination group(s) to show. Default: storage targets only, so
        # worklist/MPPS servers don't appear in the storage-oriented pickers.
        self.groups = tuple(groups) if groups else (DEST_GROUP_STORAGE,)
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
        self._nodes = sorted(
            (n for n in self.app.destinations
             if (n.group or DEST_GROUP_STORAGE) in self.groups),
            key=lambda n: n.name.lower())
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
            # HL7/MLLP endpoints have no AE title - show just host:port.
            endpoint = f"{node.host}:{node.port}"
            self.detail.configure(
                text=f"{node.aetitle} @ {endpoint}" if node.aetitle
                else endpoint)
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


class StepWizard(ctk.CTkFrame):
    """A reusable Back/Next step wizard for dense tools.

    Give it the step titles; it lays out a step header, a content area holding
    one stacked frame per step (populate via :meth:`step`), and a Back/Next nav
    bar. On the last step, Next becomes the finish button and calls
    ``on_finish``. An optional ``validate(step_index) -> (ok, message)`` gates
    advancing (and finishing), so the user can't skip ahead with an incomplete
    step; the message shows next to the buttons.

    Usage::

        wiz = StepWizard(body, ["Pick files", "Set options", "Run"],
                         on_finish=self._run, finish_text="Run",
                         validate=self._validate_step)
        wiz.grid(row=0, column=0, sticky="nsew")
        # build each step's widgets into wiz.step(0), wiz.step(1), ...
    """

    def __init__(self, master, titles, *, on_finish, finish_text="Finish",
                 validate=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._titles = list(titles)
        self._on_finish = on_finish
        self._finish_text = finish_text
        self._validate = validate
        self._i = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._head = ctk.CTkLabel(
            self, anchor="w", font=ctk.CTkFont(size=15, weight="bold"))
        self._head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(0, 4))

        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.grid(row=1, column=0, sticky="nsew")
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        self._steps = []
        for _ in self._titles:
            f = ctk.CTkFrame(holder, fg_color="transparent")
            f.grid(row=0, column=0, sticky="nsew")
            f.grid_columnconfigure(0, weight=1)
            self._steps.append(f)

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(6, 0))
        self._msg = ctk.CTkLabel(nav, text="", text_color=MUTED, anchor="w")
        self._msg.pack(side="left")
        self._next = ctk.CTkButton(nav, text="Next", width=120,
                                   command=self._go_next)
        self._next.pack(side="right")
        self._back = ctk.CTkButton(nav, text="Back", width=90,
                                   fg_color="transparent", border_width=1,
                                   command=self._go_back)
        self._back.pack(side="right", padx=PAD)
        self.show(0)

    def step(self, i: int) -> ctk.CTkFrame:
        """The parent frame to build step ``i``'s widgets into."""
        return self._steps[i]

    @property
    def index(self) -> int:
        return self._i

    def show(self, i: int) -> None:
        self._i = max(0, min(i, len(self._steps) - 1))
        self._steps[self._i].tkraise()
        self._head.configure(
            text=f"Step {self._i + 1} of {len(self._steps)}:  "
                 f"{self._titles[self._i]}")
        self._back.configure(state="normal" if self._i else "disabled")
        last = self._i == len(self._steps) - 1
        self._next.configure(text=self._finish_text if last else "Next")
        self._msg.configure(text="")

    def set_message(self, text: str, error: bool = False) -> None:
        self._msg.configure(text=text,
                            text_color=("#d9695f" if error else MUTED))

    def set_busy(self, busy: bool) -> None:
        """Disable the nav (e.g. while the final action runs)."""
        state = "disabled" if busy else "normal"
        self._next.configure(state=state)
        self._back.configure(
            state="disabled" if (busy or not self._i) else "normal")

    def _go_next(self) -> None:
        if self._validate:
            ok, msg = self._validate(self._i)
            if not ok:
                self.set_message(msg or "Complete this step first.", error=True)
                return
        if self._i == len(self._steps) - 1:
            self._on_finish()
        else:
            self.show(self._i + 1)

    def _go_back(self) -> None:
        self.show(self._i - 1)
