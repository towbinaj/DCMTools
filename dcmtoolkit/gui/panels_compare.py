"""Compare DICOM tags between two objects - two images, two series, or two
studies - and show the differences.

For series/study modes you load a folder (or files) for each side - the SAME
folder or different ones - and pick the specific series/study from a per-side
dropdown, so co-mingled exports can be compared series-by-series.
"""

from __future__ import annotations

import csv
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..tools import compare, tagclass
from .base import ToolPanel
from .theme import MUTED
from .widgets import run_threaded, PAD

_MODES = {"Images (2 files)": "images",
          "Series": "series",
          "Studies": "studies"}
_STATUS = {"different": "DIFF", "only_a": "A only", "only_b": "B only",
           "same": "same"}


class ComparePanel(ToolPanel):
    title = "Compare"
    description = ("Compare the DICOM tags of two images, two series, or two "
                  "studies and see what differs.")

    def build(self) -> None:
        self._paths = {"a": [], "b": []}
        self._groups = {"a": [], "b": []}
        self._diff = []
        self._summ = {"a": None, "b": None}
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(3, weight=1)
        self.log.grid_remove()

        top = ctk.CTkFrame(self.body)
        top.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Compare").grid(row=0, column=0, padx=PAD,
                                               pady=PAD, sticky="w")
        self.mode = ctk.CTkOptionMenu(top, values=list(_MODES),
                                      command=lambda _v: self._mode_changed())
        self.mode.grid(row=0, column=1, sticky="w", pady=PAD)

        self.sel = {}
        self.lbl = {}
        r = 1
        for side in ("a", "b"):
            ctk.CTkButton(top, text=f"Choose {side.upper()}...", width=110,
                          command=lambda s=side: self._choose(s)).grid(
                row=r, column=0, padx=PAD, pady=(0, 2))
            self.lbl[side] = ctk.CTkLabel(top, text=f"({side.upper()} not set)",
                                          text_color=MUTED, anchor="w")
            self.lbl[side].grid(row=r, column=1, sticky="ew", padx=PAD,
                                pady=(0, 2))
            self.sel[side] = ctk.CTkOptionMenu(top, values=["(load a folder)"],
                                               dynamic_resizing=False, width=460)
            self.sel[side].grid(row=r + 1, column=1, sticky="w", padx=PAD,
                                pady=(0, PAD))
            self.app.enable_drop(self.lbl[side],
                                 lambda p, s=side: self._set(s, p))
            r += 2

        # Compare + filters
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=PAD)
        self.cmp_btn = ctk.CTkButton(bar, text="Compare", command=self._run)
        self.cmp_btn.pack(side="left")
        self.only_diff = ctk.CTkCheckBox(bar, text="Only differences",
                                         command=self._render)
        self.only_diff.select()
        self.only_diff.pack(side="left", padx=PAD)
        ctk.CTkButton(bar, text="Export CSV...", width=120,
                      command=self._export).pack(side="right")
        self.search = ctk.CTkEntry(bar, placeholder_text="filter text...",
                                   width=220)
        self.search.pack(side="right", padx=PAD)
        self.search.bind("<KeyRelease>", lambda _e: self._render())

        classbar = ctk.CTkFrame(self.body, fg_color="transparent")
        classbar.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(2, 0))
        ctk.CTkLabel(classbar, text="Classes:").pack(side="left", padx=(0, PAD))
        self.class_vars = {}
        for cls in tagclass.CLASSES:
            cb = ctk.CTkCheckBox(classbar, text=cls, width=20,
                                 command=self._render)
            cb.select()
            cb.pack(side="left", padx=4)
            self.class_vars[cls] = cb

        self.out = ctk.CTkTextbox(self.body, wrap="none",
                                  font=ctk.CTkFont(family="Consolas", size=12))
        self.out.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.out.configure(state="disabled")
        self.summary = ctk.CTkLabel(self.body, text="", anchor="w",
                                    text_color=MUTED)
        self.summary.grid(row=4, column=0, sticky="w", padx=PAD, pady=(0, PAD))

        self._mode_changed()

    # -- mode / loading --------------------------------------------------
    def _mode(self) -> str:
        return _MODES[self.mode.get()]

    def _mode_changed(self) -> None:
        self._paths = {"a": [], "b": []}
        self._groups = {"a": [], "b": []}
        picks = self._mode() != "images"
        for side in ("a", "b"):
            self.lbl[side].configure(text=f"({side.upper()} not set)",
                                     text_color=MUTED)
            if picks:
                self.sel[side].grid()
                self.sel[side].configure(values=["(load a folder)"])
                self.sel[side].set("(load a folder)")
            else:
                self.sel[side].grid_remove()

    def _choose(self, side: str) -> None:
        if self._mode() == "images":
            p = filedialog.askopenfilename(
                filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
            paths = [p] if p else []
        else:
            d = filedialog.askdirectory(
                title=f"Folder for {side.upper()} "
                      f"({'series' if self._mode()=='series' else 'study'})")
            paths = [d] if d else []
        if paths:
            self._set(side, paths)

    def _set(self, side: str, paths) -> None:
        paths = [str(p) for p in paths]
        self._paths[side] = paths
        shown = Path(paths[0]).name if paths else "(none)"
        self.lbl[side].configure(text=f"{side.upper()}: {shown}",
                                 text_color=("gray10", "gray90"))
        if self._mode() != "images":
            self._enumerate(side, paths)

    def _enumerate(self, side: str, paths) -> None:
        level = self._mode()
        self.sel[side].configure(values=["scanning..."])
        self.sel[side].set("scanning...")

        def work():
            return compare.enumerate_groups(paths, level)

        def done(groups):
            self._groups[side] = groups
            labels = [g.label for g in groups] or ["(no DICOM found)"]
            self.sel[side].configure(values=labels)
            self.sel[side].set(labels[0])

        def err(exc, tb):
            self.sel[side].configure(values=[f"error: {exc}"])
            self.sel[side].set(f"error: {exc}")

        run_threaded(self, work, done, err)

    # -- compare ---------------------------------------------------------
    def _resolve(self, side: str):
        """(dataset, SideSummary) for a side, read on the UI thread."""
        mode = self._mode()
        paths = self._paths[side]
        if not paths:
            raise ValueError(f"Choose {side.upper()} first.")
        if mode == "images":
            ds = compare.read_one(paths[0])
            return ds, compare.SideSummary(label=side.upper(), instances=1,
                                           series=1, studies=1)
        groups = self._groups[side]
        sel = self.sel[side].get()
        g = next((x for x in groups if x.label == sel), None)
        if g is None:
            raise ValueError(f"Pick a {mode[:-1]} for {side.upper()}.")
        return g.representative, compare.SideSummary(
            label=side.upper(), instances=g.instances,
            series=(1 if mode == "series" else g.series),
            studies=1)

    def _run(self) -> None:
        try:
            da, sa = self._resolve("a")
            db, sb = self._resolve("b")
        except ValueError as exc:
            self._set_out(str(exc))
            return
        self.cmp_btn.configure(state="disabled")
        self._set_out("Comparing...")

        def work():
            return compare.diff_datasets(da, db), sa, sb

        def done(result):
            self._diff, self._summ["a"], self._summ["b"] = result
            self._render()
            self.cmp_btn.configure(state="normal")

        def err(exc, tb):
            self._set_out(f"[ERROR] {exc}")
            self.cmp_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _render(self) -> None:
        if not self._diff:
            return
        only_diff = bool(self.only_diff.get())
        sel = {c for c, v in self.class_vars.items() if v.get()}
        terms = [t for t in self.search.get().lower().split() if t]
        hdr = (f"{'Status':<7}{'Class':<13}{'Tag':<13}{'Keyword':<26}"
               f"{'A':<36}{'B':<36}")
        lines = [hdr, "-" * len(hdr)]
        counts = {"different": 0, "only_a": 0, "only_b": 0, "same": 0}
        for r in self._diff:
            counts[r.status] += 1
            if only_diff and r.status == "same":
                continue
            if r.cls not in sel:
                continue
            hay = f"{r.tag} {r.keyword} {r.a} {r.b} {r.cls}".lower()
            if not all(t in hay for t in terms):
                continue
            lines.append(f"{_STATUS[r.status]:<7}{r.cls:<13}{r.tag:<13}"
                         f"{r.keyword[:25]:<26}{r.a[:35]:<36}{r.b[:35]:<36}")
        self._set_out("\n".join(lines))
        sa, sb = self._summ["a"], self._summ["b"]
        self.summary.configure(
            text=f"A: {sa.instances} img   |   B: {sb.instances} img   |   "
                 f"{counts['different']} different, {counts['only_a']} A-only, "
                 f"{counts['only_b']} B-only, {counts['same']} same")

    def _set_out(self, text: str) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.configure(state="disabled")

    def _export(self) -> None:
        if not self._diff:
            messagebox.showinfo("Compare", "Nothing to export - run a compare "
                                "first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export comparison", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")], initialfile="comparison.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["status", "class", "tag", "keyword", "A", "B"])
                for r in self._diff:
                    w.writerow([r.status, r.cls, r.tag, r.keyword, r.a, r.b])
        except OSError as exc:
            messagebox.showerror("Compare", f"Export failed:\n{exc}", parent=self)
            return
        messagebox.showinfo("Compare", f"Exported {len(self._diff)} row(s) to:\n"
                            f"{path}", parent=self)
