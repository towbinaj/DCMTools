"""Compare DICOM tags between two objects - two images, two series, or two
studies - and show the differences."""

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
          "Series (2 folders)": "series",
          "Studies (2 folders)": "studies"}
_STATUS = {"different": "DIFF", "only_a": "A only", "only_b": "B only",
           "same": "same"}
_STATUS_COLOR = {"different": "#d9a441", "only_a": "#4aa3a0",
                 "only_b": "#b08cd8", "same": MUTED}


class ComparePanel(ToolPanel):
    title = "Compare"
    description = ("Compare the DICOM tags of two images, two series, or two "
                  "studies and see what differs.")

    def build(self) -> None:
        self._a_paths: list = []
        self._b_paths: list = []
        self._diff: list = []
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(3, weight=1)
        self.log.grid_remove()

        # Mode + pick A / B ------------------------------------------
        top = ctk.CTkFrame(self.body)
        top.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        top.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(top, text="Compare").grid(row=0, column=0, padx=PAD,
                                               pady=PAD)
        self.mode = ctk.CTkOptionMenu(top, values=list(_MODES),
                                      command=lambda _v: self._mode_changed())
        self.mode.grid(row=0, column=1, sticky="w", pady=PAD)

        ctk.CTkButton(top, text="Choose A...", width=110,
                      command=lambda: self._choose("a")).grid(row=1, column=0,
                                                              padx=PAD)
        self.a_lbl = ctk.CTkLabel(top, text="(A not set)", text_color=MUTED,
                                  anchor="w")
        self.a_lbl.grid(row=1, column=1, columnspan=3, sticky="ew", padx=PAD)
        ctk.CTkButton(top, text="Choose B...", width=110,
                      command=lambda: self._choose("b")).grid(row=2, column=0,
                                                              padx=PAD,
                                                              pady=(0, PAD))
        self.b_lbl = ctk.CTkLabel(top, text="(B not set)", text_color=MUTED,
                                  anchor="w")
        self.b_lbl.grid(row=2, column=1, columnspan=3, sticky="ew", padx=PAD,
                        pady=(0, PAD))
        self._enable_drop_targets()

        # Compare + filters ------------------------------------------
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

    # -- loading ---------------------------------------------------------
    def _mode(self) -> str:
        return _MODES[self.mode.get()]

    def _mode_changed(self) -> None:
        # Switching between file/folder modes clears prior selections.
        self._a_paths = []; self._b_paths = []
        self.a_lbl.configure(text="(A not set)")
        self.b_lbl.configure(text="(B not set)")

    def _enable_drop_targets(self) -> None:
        self.app.enable_drop(self.a_lbl, lambda p: self._set("a", p))
        self.app.enable_drop(self.b_lbl, lambda p: self._set("b", p))

    def _choose(self, side: str) -> None:
        if self._mode() == "images":
            p = filedialog.askopenfilename(
                filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
            paths = [p] if p else []
        else:
            d = filedialog.askdirectory(
                title=f"Select {'series' if self._mode()=='series' else 'study'}"
                      f" folder for {side.upper()}")
            paths = [d] if d else []
        if paths:
            self._set(side, paths)

    def _set(self, side: str, paths) -> None:
        paths = [str(p) for p in paths]
        lbl = self.a_lbl if side == "a" else self.b_lbl
        if side == "a":
            self._a_paths = paths
        else:
            self._b_paths = paths
        shown = Path(paths[0]).name if paths else "(none)"
        lbl.configure(text=f"{side.upper()}: {shown}",
                      text_color=("gray10", "gray90"))

    # -- compare ---------------------------------------------------------
    def _run(self) -> None:
        if not self._a_paths or not self._b_paths:
            self._set_out("Choose both A and B first.")
            return
        mode = self._mode()
        a_paths, b_paths = list(self._a_paths), list(self._b_paths)
        self.cmp_btn.configure(state="disabled")
        self._set_out("Comparing...")

        def work():
            da, sa = compare.load_side(a_paths, mode=mode, label="A")
            db, sb = compare.load_side(b_paths, mode=mode, label="B")
            return compare.diff_datasets(da, db), sa, sb

        def done(result):
            self._diff, sa, sb = result
            self._summ_a, self._summ_b = sa, sb
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
        sa, sb = self._summ_a, self._summ_b
        note = "  ".join(n for n in (sa.note, sb.note) if n)
        self.summary.configure(
            text=f"A: {sa.instances} inst / {sa.series} series   |   "
                 f"B: {sb.instances} inst / {sb.series} series   |   "
                 f"{counts['different']} different, {counts['only_a']} A-only, "
                 f"{counts['only_b']} B-only, {counts['same']} same"
                 + (f"   |   {note}" if note else ""))

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
