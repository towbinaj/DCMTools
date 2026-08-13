"""Reusable batch-progress engine shared by the file/network batch tools.

A tool creates a :class:`BatchRunner`, grids the widget it builds, and calls
``run()`` with a worker that invokes a core function passing ``runner.on_item``
as its per-item callback. The runner then:

* throttles UI updates (a 300ms poller) so per-item events never flood Tk,
* shows count / %, rate, ETA, elapsed, current item, and a stall warning,
* summarizes per-folder results and lists only folders that had failures,
* writes an incremental per-folder report CSV (survives a crash) plus a
  failures CSV,
* offers "Retry failed" (in-memory or from a prior failures CSV).

The item callback signature is uniform: ``on_item(index, total, path, ok,
detail)``.
"""

from __future__ import annotations

import csv
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .. import paths
from .theme import MUTED
from .widgets import run_threaded, PAD


def read_failures_csv(path: Path) -> list[str]:
    out: list[str] = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader, None)  # header
            for row in reader:
                if row and row[0].strip():
                    out.append(row[0].strip())
    except OSError:
        pass
    return out


class BatchRunner:
    def __init__(self, panel, verb: str = "done"):
        self.panel = panel          # a ToolPanel: has .log, .after, requeue()
        self.verb = verb            # past-tense count label: sent/written/...
        self.cancelled = False
        self._lock = threading.Lock()
        self._stats = None
        self._failed_paths: list[Path] = []
        self._errors: list[str] = []
        self._vbuf: list[str] = []
        self._logged_folders: set = set()
        self._report_fh = None
        self._report_writer = None
        self.report_path = None
        self.failures_path = None
        self._on_done = None

    # -- widgets ---------------------------------------------------------
    def build(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(5, weight=1)

        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.verbose = ctk.CTkCheckBox(bar, text="Log every file")
        self.verbose.pack(side="left")
        self.save_btn = ctk.CTkButton(bar, text="Save failures CSV...",
                                      width=150, state="disabled",
                                      command=self._save_failures)
        self.save_btn.pack(side="right")
        self.retry_btn = ctk.CTkButton(bar, text="Retry failed...", width=120,
                                       command=self._retry)
        self.retry_btn.pack(side="right", padx=PAD)

        self.progress = ctk.CTkProgressBar(f)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.status_lbl = ctk.CTkLabel(
            f, text="Idle.", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.status_lbl.grid(row=2, column=0, sticky="w")
        self.detail_lbl = ctk.CTkLabel(f, text="", anchor="w",
                                       text_color=MUTED)
        self.detail_lbl.grid(row=3, column=0, sticky="w")
        self.summary_lbl = ctk.CTkLabel(
            f, text="", anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        self.summary_lbl.grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.results = ctk.CTkScrollableFrame(
            f, label_text="Folders needing attention (failures)")
        self.results.grid(row=5, column=0, sticky="nsew", pady=(2, 0))
        self.results.grid_columnconfigure(0, weight=1)
        self._result_rows: dict = {}
        self.frame = f
        return f

    # -- lifecycle -------------------------------------------------------
    def cancel(self) -> None:
        self.cancelled = True
        self.panel.log.write("Cancelling (finishing current file)...")

    def failed_paths(self) -> list[Path]:
        return list(self._failed_paths)

    def run(self, total: int, report_prefix: str, worker, on_done=None) -> None:
        self.cancelled = False
        self._failed_paths = []
        self._errors = []
        self._vbuf = []
        self._logged_folders = set()
        self._on_done = on_done
        self.progress.set(0)
        for w in self.results.winfo_children():
            w.destroy()
        self._result_rows = {}
        self.summary_lbl.configure(text="")
        self.save_btn.configure(state="disabled")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.report_path = paths.reports_dir() / f"{report_prefix}_{stamp}_folders.csv"
        self.failures_path = paths.reports_dir() / f"{report_prefix}_{stamp}_failures.csv"
        try:
            self._report_fh = open(self.report_path, "w", newline="",
                                   encoding="utf-8")
            self._report_writer = csv.writer(self._report_fh)
            self._report_writer.writerow(["folder", self.verb, "failed",
                                          "status"])
        except OSError:
            self._report_fh = self._report_writer = None

        self._stats = {"i": 0, "total": total, "ok": 0, "failed": 0,
                       "name": "", "folder": "", "start": time.monotonic(),
                       "last": time.monotonic(), "done": False,
                       "folders": {}, "order": []}

        def _done(_result):
            with self._lock:
                self._stats["done"] = True
            self._finalize()

        def _err(exc, tb):
            with self._lock:
                if self._stats:
                    self._stats["done"] = True
            self.panel.log.write(f"[ERROR] {exc}")
            self._finalize()

        run_threaded(self.panel, worker, _done, _err)
        self.panel.after(300, self._tick)

    # -- worker-thread callback -----------------------------------------
    def on_item(self, i: int, total: int, path, ok: bool,
                detail: str = "") -> None:
        p = Path(path)
        folder = str(p.parent)
        verbose = bool(self.verbose.get())
        with self._lock:
            s = self._stats
            if s is None:
                return
            s["i"] = i
            s["name"] = p.name
            s["folder"] = folder
            s["last"] = time.monotonic()
            fc = s["folders"].get(folder)
            if fc is None:
                fc = [0, 0]
                s["folders"][folder] = fc
                s["order"].append(folder)
            if ok:
                s["ok"] += 1
                fc[0] += 1
            else:
                s["failed"] += 1
                fc[1] += 1
                self._failed_paths.append(p)
                if len(self._errors) < 5000:
                    self._errors.append(f"{p}: {detail}")
            if verbose:
                tag = "OK" if ok else "FAIL"
                self._vbuf.append(f"[{i}/{total}] {tag} {p.name} ({detail})")

    # -- UI poller -------------------------------------------------------
    @staticmethod
    def _fmt(secs: float) -> str:
        secs = int(secs)
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _tick(self) -> None:
        s = self._stats
        if not s:
            return
        with self._lock:
            i, total = s["i"], s["total"]
            ok, failed = s["ok"], s["failed"]
            name, folder = s["name"], s["folder"]
            start, last, done = s["start"], s["last"], s["done"]
            order = list(s["order"])
            folders = {k: list(v) for k, v in s["folders"].items()}
            vlines = self._vbuf
            self._vbuf = []

        for ln in vlines[-800:]:   # batched verbose flush (never per-item)
            self.panel.log.write(ln)

        now = time.monotonic()
        frac = i / total if total else 0
        self.progress.set(frac)
        elapsed = now - start
        rate = i / elapsed if elapsed > 0 else 0
        txt = (f"{i:,}/{total:,}  ({frac * 100:.1f}%)    {rate:.0f}/s    "
               f"{self.verb} {ok:,}   failed {failed:,}    "
               f"{self._fmt(elapsed)} elapsed")
        if rate > 0 and not done:
            txt += f"    ETA {self._fmt((total - i) / rate)}"
        self.status_lbl.configure(text=txt)

        cur = f"{Path(folder).name}/{name}" if folder else name
        stale = now - last
        detail = f"current: {cur}" if cur else ""
        if not done and stale > 8:
            detail += f"      no activity for {int(stale)}s"
        self.detail_lbl.configure(
            text_color="#d9a441" if (not done and stale > 8) else MUTED)
        self.detail_lbl.configure(text=detail)

        finished = order if done else order[:-1]
        for fo in finished:
            if fo not in self._logged_folders:
                self._logged_folders.add(fo)
                oc, fcnt = folders[fo]
                mark = "OK " if fcnt == 0 else "!! "
                self.panel.log.write(f"{mark}{Path(fo).name}: {self.verb} "
                                     f"{oc}, failed {fcnt}")
                if self._report_writer:
                    self._report_writer.writerow(
                        [fo, oc, fcnt,
                         "complete" if fcnt == 0 else "has_failures"])
                    self._report_fh.flush()

        done_ct = len(finished)
        clean = sum(1 for fo in finished if folders[fo][1] == 0)
        bad = [fo for fo in order if folders[fo][1] > 0]
        self.summary_lbl.configure(
            text=f"Folders — done: {done_ct}   clean: {clean}   "
                 f"with failures: {len(bad)}   in progress: "
                 f"{len(order) - done_ct}")
        for fo in bad:
            oc, fcnt = folders[fo]
            self._upsert_row(fo, oc, fcnt)

        if not done:
            self.panel.after(300, self._tick)

    def _upsert_row(self, folder: str, ok: int, failed: int) -> None:
        text = f"{Path(folder).name}    {self.verb} {ok}, failed {failed}"
        existing = self._result_rows.get(folder)
        if existing is None:
            lbl = ctk.CTkLabel(self.results, text=text, anchor="w",
                               text_color="#d9695f")
            lbl.grid(sticky="ew", padx=4, pady=1)
            self._result_rows[folder] = lbl
        else:
            existing.configure(text=text)

    def _finalize(self) -> None:
        self._tick()
        s = self._stats or {}
        self.panel.log.write(f"[DONE] {self.verb} {s.get('ok', 0):,}, "
                             f"failed {s.get('failed', 0):,}.")
        if self._report_fh:
            try:
                self._report_fh.close()
            except OSError:
                pass
        if self._errors:
            try:
                with open(self.failures_path, "w", newline="",
                          encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(["file", "reason"])
                    for e in self._errors:
                        fp, _, reason = str(e).partition(": ")
                        w.writerow([fp, reason])
            except OSError:
                pass
            self.save_btn.configure(state="normal")
            self.panel.log.write(f"Report + failures CSV saved to "
                                 f"{paths.reports_dir()}")
        else:
            self.panel.log.write(f"No failures. Folder report saved to "
                                 f"{self.report_path}")
        if self._on_done:
            self._on_done()

    # -- buttons ---------------------------------------------------------
    def _save_failures(self) -> None:
        if not self._errors:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="failures.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "reason"])
            for e in self._errors:
                fp, _, reason = str(e).partition(": ")
                w.writerow([fp, reason])
        self.panel.log.write(f"Saved {len(self._errors):,} failure(s) to "
                             f"{path}")

    def _retry(self) -> None:
        retry = [str(p) for p in self._failed_paths]
        if not retry:
            csv_path = filedialog.askopenfilename(
                title="Open a failures CSV to retry",
                initialdir=str(paths.reports_dir()),
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
            if not csv_path:
                return
            retry = read_failures_csv(Path(csv_path))
        existing = [Path(p) for p in retry if Path(p).exists()]
        missing = len(retry) - len(existing)
        if not existing:
            self.panel.log.write("No failed files available to retry.")
            return
        self.panel.requeue(existing)
        msg = f"Queued {len(existing):,} failed file(s) for retry."
        if missing:
            msg += f" ({missing:,} no longer on disk, skipped.)"
        self.panel.log.write(msg + " Press the action button to run.")
