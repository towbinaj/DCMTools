"""QA / Consistency check panel."""

from __future__ import annotations

import csv
import threading
import time
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..tools import qa
from .base import ToolPanel
from .panels_files import _DropSourceMixin
from .theme import MUTED
from .widgets import run_threaded, PAD

_CAT_COLOR = {
    "incongruent": "#d9a441",
    "abnormal": "#d9695f",
    "missing": "#d98a5f",
    "duplicate": "#b08cd8",
    "mismatch": "#e0894a",
}
_CATEGORIES = ["All", "incongruent", "abnormal", "missing", "duplicate",
               "mismatch"]


class QAPanel(_DropSourceMixin, ToolPanel):
    title = "QA / Consistency"
    description = ("Scan files for incongruent, abnormal, missing, or "
                  "duplicate tags.")
    # Each load replaces the queue, so a new folder is scanned on its own
    # (not piled together with the previously loaded one).
    LOAD_REPLACES = True

    def build(self) -> None:
        self.files: list[Path] = []
        self._findings: list = []
        self._cancel = False
        self._scan = None
        self._lock = threading.Lock()

        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(4, weight=1)

        self._scanned = False
        self._make_source("Scan").grid(row=0, column=0, sticky="ew",
                                       padx=PAD, pady=PAD)
        self.count_lbl.configure(font=ctk.CTkFont(size=13, weight="bold"))

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        self.btn = ctk.CTkButton(bar, text="Scan", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(bar, text="Cancel", width=80,
                                        command=self._do_cancel,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)
        self._workers_control(bar)
        ctk.CTkLabel(bar, text="Show").pack(side="left", padx=(PAD, 4))
        self.filter = ctk.CTkOptionMenu(bar, values=_CATEGORIES, width=130,
                                        command=lambda _v: self._render())
        self.filter.set("All")
        self.filter.pack(side="left")
        self.export_btn = ctk.CTkButton(bar, text="Export CSV...", width=120,
                                        state="disabled",
                                        command=self._export)
        self.export_btn.pack(side="right")

        statusf = ctk.CTkFrame(self.body, fg_color="transparent")
        statusf.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        statusf.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(statusf)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.status = ctk.CTkLabel(statusf, text="Add files or a folder, then "
                                   "Scan.", anchor="w",
                                   font=ctk.CTkFont(size=14, weight="bold"))
        self.status.grid(row=1, column=0, sticky="w")

        self.summary = ctk.CTkLabel(self.body, text="", anchor="w",
                                    text_color=MUTED)
        self.summary.grid(row=3, column=0, sticky="w", padx=PAD)

        self.results = ctk.CTkScrollableFrame(self.body, label_text="Findings")
        self.results.grid(row=4, column=0, sticky="nsew", padx=PAD,
                          pady=(2, PAD))
        self.results.grid_columnconfigure(0, weight=1)

    # -- source ----------------------------------------------------------
    def _update_count(self):  # override: flag that shown findings are stale
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")
        if self._scanned:
            # The loaded set changed after a scan - the findings below no
            # longer describe what's loaded, so say so plainly.
            self._scanned = False
            self.status.configure(
                text=f"Loaded {len(self.files):,} file(s) — changed since last "
                     "scan. Press Scan to refresh.", text_color="#d9a441")

    def _clear(self):
        self.files = []
        self._findings = []
        self._scanned = False
        for w in self.results.winfo_children():
            w.destroy()
        self.summary.configure(text="")
        self.export_btn.configure(state="disabled")
        self.progress.set(0)
        self.count_lbl.configure(text="No files.")
        self.status.configure(text="Add files or a folder, then Scan.",
                              text_color=("gray10", "gray90"))

    # -- scan ------------------------------------------------------------
    def _do_cancel(self):
        self._cancel = True
        self.log.write("Cancelling scan...")

    def _run(self):
        if not self.files:
            self.log.write("Add files or a folder first.")
            return
        self._cancel = False
        self._findings = []
        for w in self.results.winfo_children():
            w.destroy()
        self.summary.configure(text="")
        self.export_btn.configure(state="disabled")
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.set(0)

        files = list(self.files)
        total = len(files)
        workers = self._get_workers()
        self._scan = {"i": 0, "total": total}
        self.status.configure(text=f"Scanning 0/{total:,} ...",
                              text_color=("gray10", "gray90"))

        def on_item(i, tot, f, ok, detail):
            with self._lock:
                self._scan["i"] = i

        def worker():
            return qa.qa_scan(files, on_item=on_item,
                              should_cancel=lambda: self._cancel,
                              workers=workers)

        def done(result):
            self._findings = result.findings
            self._scanned = True
            self._render()
            n = len(result.findings)
            if self._cancel:
                self.status.configure(
                    text=f"■ Scan cancelled — {result.files_read:,} file(s) "
                         f"read, {n:,} finding(s) so far.",
                    text_color="#d9a441")
            elif n:
                self.status.configure(
                    text=f"✓ Scan complete — {result.files_read:,} file(s), "
                         f"{n:,} finding(s).", text_color="#d9a441")
            else:
                self.status.configure(
                    text=f"✓ Scan complete — {result.files_read:,} file(s), "
                         "no issues found.",
                    text_color=("#2e8b57", "#43c59e"))
            self.progress.set(1.0)
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            self.export_btn.configure(
                state="normal" if result.findings else "disabled")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")

        run_threaded(self, worker, done, err)
        self.after(200, self._tick)

    def _tick(self):
        s = self._scan
        if not s:
            return
        with self._lock:
            i, total = s["i"], s["total"]
        if i < total and self.cancel_btn.cget("state") == "normal":
            self.progress.set(i / total if total else 0)
            self.status.configure(text=f"Scanning {i:,}/{total:,} ...")
            self.after(200, self._tick)

    # -- results ---------------------------------------------------------
    def _render(self):
        for w in self.results.winfo_children():
            w.destroy()
        cat = self.filter.get()
        shown = [f for f in self._findings if cat == "All" or f.category == cat]

        from collections import Counter
        counts = Counter(f.category for f in self._findings)
        self.summary.configure(
            text="   ".join(f"{c}: {counts.get(c, 0)}"
                            for c in _CATEGORIES[1:]) or "No findings.")

        if not shown:
            ctk.CTkLabel(self.results, text="No findings in this category.",
                         text_color=MUTED).grid(sticky="w", padx=4, pady=4)
            return
        limit = 500
        for f in shown[:limit]:
            row = ctk.CTkFrame(self.results, fg_color="transparent")
            row.grid(sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"[{f.category}]  {f.summary}", anchor="w",
                         text_color=_CAT_COLOR.get(f.category, MUTED),
                         font=ctk.CTkFont(weight="bold")).grid(
                row=0, column=0, sticky="w")
            if f.detail:
                ctk.CTkLabel(row, text=f"      {f.detail}", anchor="w",
                             text_color=MUTED, justify="left").grid(
                    row=1, column=0, sticky="w")
        if len(shown) > limit:
            ctk.CTkLabel(self.results,
                         text=f"... and {len(shown) - limit:,} more "
                              "(Export CSV for the full list).",
                         text_color=MUTED).grid(sticky="w", padx=4, pady=4)

    def _export(self):
        if not self._findings:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="qa_findings.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["category", "summary", "detail"])
            for f in self._findings:
                w.writerow([f.category, f.summary, f.detail])
        self.log.write(f"Exported {len(self._findings):,} finding(s) to {path}")
