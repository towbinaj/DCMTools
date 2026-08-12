"""Network tool panels: Echo, Echo-All, Send, Query/Move, Retrieve, Commit."""

from __future__ import annotations

import csv
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from pydicom import dcmread

from .. import paths
from ..net import scu
from ..tools.fileops import find_dicom_files
from .base import ToolPanel
from .theme import MUTED
from .widgets import DestinationPicker, run_threaded, section, PAD


class EchoPanel(ToolPanel):
    title = "C-Echo"
    description = "Verify connectivity to a remote DICOM node (DICOM ping)."

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app,
                                        remember_key="EchoPanel")
        self.picker.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)

        self.btn = ctk.CTkButton(self.body, text="Send C-ECHO",
                                 command=self._run)
        self.btn.grid(row=1, column=0, sticky="w", padx=PAD, pady=(0, PAD))

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _run(self) -> None:
        node = self.picker.get_node()
        if not node:
            self.log.write("No destination selected.")
            return
        self.btn.configure(state="disabled")
        self.log.write(f"--- Echo to {node.name} ---")
        my_ae = self.app.settings.my_aetitle
        tls = self.app.tls_args_for(node)

        def work():
            return scu.c_echo(my_ae, node, timeout=node.timeout,
                              progress=self.progress, tls_args=tls)

        def done(result):
            mark = "OK" if result.success else "FAIL"
            self.log.write(f"[{mark}] {result.message}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)


class EchoAllPanel(ToolPanel):
    title = "Echo All (health)"
    description = ("Ping every saved destination with C-ECHO and show which are "
                  "reachable.")

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.test_btn = ctk.CTkButton(bar, text="Test All",
                                      command=self._test_all)
        self.test_btn.pack(side="left")
        self.summary = ctk.CTkLabel(bar, text="", text_color=MUTED)
        self.summary.pack(side="left", padx=PAD)

        self.table = ctk.CTkScrollableFrame(self.body, height=340)
        self.table.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.table.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, weight=1)
        # Hide the log box; the table is the output.
        self.log.grid_remove()

        self._rows: dict[str, ctk.CTkLabel] = {}
        self._build_rows()

    def on_destinations_changed(self) -> None:
        self._build_rows()

    def _build_rows(self) -> None:
        for child in self.table.winfo_children():
            child.destroy()
        self._rows = {}
        hdr = ctk.CTkFrame(self.table, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Destination", anchor="w",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr, text="Status", width=160, anchor="w",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=1)
        for i, node in enumerate(self.app.destinations, start=1):
            fr = ctk.CTkFrame(self.table, fg_color="transparent")
            fr.grid(row=i, column=0, sticky="ew", pady=1)
            fr.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(fr, text=f"{node.name}  ({node.aetitle}@{node.host}:"
                                  f"{node.port})", anchor="w").grid(
                row=0, column=0, sticky="w")
            status = ctk.CTkLabel(fr, text="—", width=160, anchor="w",
                                  text_color=MUTED)
            status.grid(row=0, column=1)
            self._rows[self._key(node)] = status

    @staticmethod
    def _key(node) -> str:
        return f"{node.aetitle}@{node.host}:{node.port}"

    def _test_all(self) -> None:
        nodes = list(self.app.destinations)
        if not nodes:
            return
        self.test_btn.configure(state="disabled")
        self.summary.configure(text=f"Testing 0/{len(nodes)} ...")
        my_ae = self.app.settings.my_aetitle
        done_count = {"n": 0, "ok": 0}
        lock = threading.Lock()

        for node in nodes:
            key = self._key(node)
            lbl = self._rows.get(key)
            if lbl:
                self.after(0, lambda l=lbl: l.configure(text="testing...",
                                                        text_color=MUTED))

            def work(n=node):
                t0 = time.time()
                res = scu.c_echo(my_ae, n, timeout=min(n.timeout, 15),
                                 tls_args=self.app.tls_args_for(n))
                return res, int((time.time() - t0) * 1000)

            def done(result, n=node, key=key):
                res, ms = result
                lbl = self._rows.get(key)
                if lbl:
                    if res.success:
                        lbl.configure(text=f"OK  ({ms} ms)",
                                      text_color="#2a2")
                    else:
                        lbl.configure(text="FAIL", text_color="#d33")
                with lock:
                    done_count["n"] += 1
                    if res.success:
                        done_count["ok"] += 1
                    self.summary.configure(
                        text=f"Tested {done_count['n']}/{len(nodes)}  "
                             f"({done_count['ok']} OK)")
                    if done_count["n"] == len(nodes):
                        self.test_btn.configure(state="normal")

            def err(exc, tb, key=key):
                lbl = self._rows.get(key)
                if lbl:
                    lbl.configure(text="ERROR", text_color="#d33")
                with lock:
                    done_count["n"] += 1
                    if done_count["n"] == len(nodes):
                        self.test_btn.configure(state="normal")

            run_threaded(self, work, done, err)


class SendPanel(ToolPanel):
    title = "Send (C-Store)"
    description = "Send DICOM files or a folder to a destination."

    def build(self) -> None:
        # Layout: keep the milestone log a short strip; give the per-folder
        # results table the room.
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=90)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(6, weight=1)

        self.picker = DestinationPicker(self.body, self.app,
                                        remember_key="SendPanel")
        self.picker.grid(row=0, column=0, columnspan=3, sticky="ew",
                         padx=PAD, pady=PAD)

        self.files: list[Path] = []

        # Drop zone containing the add buttons (drop files/folders anywhere on it)
        dropf = ctk.CTkFrame(self.body, border_width=2,
                             border_color=("gray70", "gray40"))
        dropf.grid(row=1, column=0, columnspan=3, sticky="ew", padx=PAD,
                   pady=PAD)
        ctk.CTkButton(dropf, text="Add Files...", command=self._add_files).pack(
            side="left", padx=6, pady=8)
        ctk.CTkButton(dropf, text="Add Folder...",
                      command=self._add_folder).pack(side="left", padx=6)
        ctk.CTkButton(dropf, text="Clear", width=70,
                      command=self._clear).pack(side="left", padx=6)
        self.drop_hint = ctk.CTkLabel(
            dropf, text="…or drag files / folders here, then press Send",
            text_color=MUTED)
        self.drop_hint.pack(side="right", padx=12)

        self.count_lbl = ctk.CTkLabel(self.body, text="No files selected.",
                                      anchor="w")
        self.count_lbl.grid(row=2, column=0, columnspan=3, sticky="w", padx=PAD)

        runbar = ctk.CTkFrame(self.body, fg_color="transparent")
        runbar.grid(row=3, column=0, columnspan=3, sticky="ew", padx=PAD,
                    pady=(PAD, 0))
        self.btn = ctk.CTkButton(runbar, text="Send", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(runbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)
        self.verbose = ctk.CTkCheckBox(runbar, text="Log every file")
        self.verbose.pack(side="left", padx=PAD)
        self.save_fail_btn = ctk.CTkButton(runbar, text="Save failures CSV...",
                                           width=150, state="disabled",
                                           command=self._save_failures)
        self.save_fail_btn.pack(side="right")
        self.retry_btn = ctk.CTkButton(runbar, text="Retry failed...",
                                       width=120, command=self._retry_failed)
        self.retry_btn.pack(side="right", padx=PAD)

        # Live status: progress bar + a compact readout + current/stall line.
        statusf = ctk.CTkFrame(self.body, fg_color="transparent")
        statusf.grid(row=4, column=0, columnspan=3, sticky="ew", padx=PAD,
                     pady=(PAD, 0))
        statusf.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(statusf)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.status_lbl = ctk.CTkLabel(
            statusf, text="Idle.", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.status_lbl.grid(row=1, column=0, sticky="w")
        self.detail_lbl = ctk.CTkLabel(statusf, text="", anchor="w",
                                       text_color=MUTED)
        self.detail_lbl.grid(row=2, column=0, sticky="w")

        # Per-folder results: summary + a table of folders that had failures.
        self.folder_summary = ctk.CTkLabel(
            self.body, text="", anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"))
        self.folder_summary.grid(row=5, column=0, columnspan=3, sticky="w",
                                 padx=PAD, pady=(PAD, 0))
        self.results = ctk.CTkScrollableFrame(
            self.body, label_text="Folders needing attention (failures)")
        self.results.grid(row=6, column=0, columnspan=3, sticky="nsew",
                          padx=PAD, pady=(2, PAD))
        self.results.grid_columnconfigure(0, weight=1)
        self._result_rows: dict = {}

        self._cancel_flag = False
        self._stats = None
        self._lock = None
        self._logged_folders: set = set()
        self._failures: list = []
        self._failed_paths: list = []
        self._report_fh = None
        self._report_writer = None
        self._report_path = None

        # Enable drag-and-drop onto the drop zone (and the table).
        dropped = self.app.enable_drop(dropf, self._on_drop)
        self.app.enable_drop(self.drop_hint, self._on_drop)
        self.app.enable_drop(self.results, self._on_drop)
        if not dropped:
            self.drop_hint.configure(text="")

    def _on_drop(self, paths: list) -> None:
        paths = [Path(p) for p in paths]
        files = [p for p in paths if p.is_file()]
        folders = [p for p in paths if p.is_dir()]
        self.files.extend(files)
        if folders:
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                out = []
                for d in folders:
                    out.extend(find_dicom_files(d))
                return out

            def done(found):
                self.files.extend(found)
                self.log.write(f"Added {len(found):,} file(s) from "
                               f"{len(folders)} dropped folder(s).")
                self._update_count()

            run_threaded(self, work, done)
        else:
            if files:
                self.log.write(f"Added {len(files):,} dropped file(s).")
            self._update_count()

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _cancel(self) -> None:
        self._cancel_flag = True
        self.log.write("Cancelling (finishing current file)...")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select DICOM files",
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in paths)
        self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder of DICOM files")
        if not folder:
            return
        self.count_lbl.configure(text=f"Scanning {folder} ...")

        def work():
            return find_dicom_files(Path(folder))

        def done(found):
            self.files.extend(found)
            self.log.write(f"Found {len(found):,} file(s) in {folder}")
            self._update_count()

        run_threaded(self, work, done)

    def _clear(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))  # de-dupe, keep order
        self.count_lbl.configure(text=f"{len(self.files):,} file(s) queued.")

    @staticmethod
    def _fmt(secs: float) -> str:
        secs = int(secs)
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _run(self) -> None:
        node = self.picker.get_node()
        if not node:
            self.log.write("No destination selected.")
            return
        if not self.files:
            self.log.write("No files queued.")
            return
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.save_fail_btn.configure(state="disabled")
        self._cancel_flag = False
        self._logged_folders = set()
        self._failures = []
        self._failed_paths = []
        self.progress_bar.set(0)
        for w in self.results.winfo_children():
            w.destroy()
        self._result_rows = {}
        self.folder_summary.configure(text="")

        my_ae = self.app.settings.my_aetitle
        files = list(self.files)
        total = len(files)
        tls = self.app.tls_args_for(node)
        verbose = bool(self.verbose.get())
        self.log.write(f"--- Sending {total:,} file(s) to {node.name} ---")

        # Open an incremental per-folder report (survives a crash mid-run).
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._report_path = paths.reports_dir() / f"send_{stamp}_folders.csv"
        self._failures_path = paths.reports_dir() / f"send_{stamp}_failures.csv"
        try:
            self._report_fh = open(self._report_path, "w", newline="",
                                   encoding="utf-8")
            self._report_writer = csv.writer(self._report_fh)
            self._report_writer.writerow(["folder", "sent", "failed", "status"])
        except OSError:
            self._report_fh = self._report_writer = None

        lock = threading.Lock()
        self._lock = lock
        self._stats = {"i": 0, "total": total, "sent": 0, "failed": 0,
                       "name": "", "folder": "", "start": time.monotonic(),
                       "last": time.monotonic(), "done": False,
                       "folders": {}, "order": []}

        def on_file(i, tot, path, ok, code):
            folder = str(path.parent)
            with lock:
                s = self._stats
                s["i"] = i
                s["name"] = path.name
                s["folder"] = folder
                s["last"] = time.monotonic()
                fc = s["folders"].get(folder)
                if fc is None:
                    fc = [0, 0]
                    s["folders"][folder] = fc
                    s["order"].append(folder)
                if ok:
                    s["sent"] += 1
                    fc[0] += 1
                else:
                    s["failed"] += 1
                    fc[1] += 1
                    self._failed_paths.append(path)

        def work():
            return scu.c_store(my_ae, node, files, progress=self.progress,
                               should_cancel=lambda: self._cancel_flag,
                               timeout=node.timeout, tls_args=tls,
                               on_file=on_file, verbose=verbose)

        def done(result):
            with lock:
                self._stats["done"] = True
            self._finalize(result)

        def err(exc, tb):
            with lock:
                if self._stats:
                    self._stats["done"] = True
            self.log.write(f"[ERROR] {exc}")
            self._end_ui()

        run_threaded(self, work, done, err)
        self.after(300, self._tick)

    def _tick(self) -> None:
        s = self._stats
        if not s:
            return
        with self._lock:
            i, total = s["i"], s["total"]
            sent, failed = s["sent"], s["failed"]
            name, folder = s["name"], s["folder"]
            start, last, done = s["start"], s["last"], s["done"]
            order = list(s["order"])
            folders = {k: list(v) for k, v in s["folders"].items()}

        now = time.monotonic()
        frac = i / total if total else 0
        self.progress_bar.set(frac)
        elapsed = now - start
        rate = i / elapsed if elapsed > 0 else 0
        txt = (f"{i:,}/{total:,}  ({frac * 100:.1f}%)    {rate:.0f}/s    "
               f"sent {sent:,}   failed {failed:,}    "
               f"{self._fmt(elapsed)} elapsed")
        if rate > 0 and not done:
            txt += f"    ETA {self._fmt((total - i) / rate)}"
        self.status_lbl.configure(text=txt)

        cur = f"{Path(folder).name}/{name}" if folder else name
        stale = now - last
        detail = f"current: {cur}" if cur else ""
        if not done and stale > 8:
            detail += f"      ⚠ no activity for {int(stale)}s"
        self.detail_lbl.configure(
            text_color="#d9a441" if (not done and stale > 8) else MUTED)
        self.detail_lbl.configure(text=detail)

        # Folders that are fully done (all but the still-active last one).
        finished = order if done else order[:-1]
        for fo in finished:
            if fo not in self._logged_folders:
                self._logged_folders.add(fo)
                sc, fcnt = folders[fo]
                mark = "OK " if fcnt == 0 else "!! "
                self.log.write(f"{mark}{Path(fo).name}: sent {sc}, "
                               f"failed {fcnt}")
                if self._report_writer:
                    self._report_writer.writerow(
                        [fo, sc, fcnt,
                         "complete" if fcnt == 0 else "has_failures"])
                    self._report_fh.flush()

        # Folder summary counts + a table listing only folders with failures.
        done_ct = len(finished)
        clean = sum(1 for fo in finished if folders[fo][1] == 0)
        bad = [fo for fo in order if folders[fo][1] > 0]
        in_prog = len(order) - done_ct
        self.folder_summary.configure(
            text=f"Folders — done: {done_ct}   clean: {clean}   "
                 f"with failures: {len(bad)}   in progress: {in_prog}")
        for fo in bad:
            sc, fcnt = folders[fo]
            self._upsert_result_row(fo, sc, fcnt)

        if not done:
            self.after(300, self._tick)

    def _upsert_result_row(self, folder: str, sent: int, failed: int) -> None:
        existing = self._result_rows.get(folder)
        text = f"{Path(folder).name}    sent {sent}, failed {failed}"
        if existing is None:
            lbl = ctk.CTkLabel(self.results, text=text, anchor="w",
                               text_color="#d9695f")
            lbl.grid(sticky="ew", padx=4, pady=1)
            self._result_rows[folder] = lbl
        else:
            existing.configure(text=text)

    def _finalize(self, result) -> None:
        self._tick()  # flush final folder lines + last progress
        self.log.write(f"[DONE] sent {result.sent:,}, failed "
                       f"{result.failed:,}, warnings {result.warnings}")
        self._failures = list(result.errors)
        if self._report_fh:
            try:
                self._report_fh.close()
            except OSError:
                pass
        # Always write a failures CSV when there are failures.
        if self._failures:
            try:
                with open(self._failures_path, "w", newline="",
                          encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(["file", "reason"])
                    for e in self._failures:
                        fp, _, reason = str(e).partition(": ")
                        w.writerow([fp, reason])
            except OSError:
                pass
            self.save_fail_btn.configure(state="normal")
            self.log.write(f"Failures: {result.failed:,}. Saved report + "
                           f"failures CSV to {paths.reports_dir()}")
        else:
            self.log.write(f"No failures. Folder report saved to "
                           f"{self._report_path}")
        self._end_ui()

    def _end_ui(self) -> None:
        self.btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def _save_failures(self) -> None:
        if not self._failures:
            return
        import csv
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="send_failures.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "reason"])
            for e in self._failures:
                file_part, _, reason = str(e).partition(": ")
                w.writerow([file_part, reason])
        self.log.write(f"Saved {len(self._failures):,} failure(s) to {path}")

    def _retry_failed(self) -> None:
        """Re-queue the failed files from the last run (or a failures CSV)."""
        retry = list(self._failed_paths)
        if not retry:
            # No in-memory failures (e.g. after reopening) - load from a CSV.
            csv_path = filedialog.askopenfilename(
                title="Open a failures CSV to retry",
                initialdir=str(paths.reports_dir()),
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
            if not csv_path:
                return
            retry = self._read_failures_csv(Path(csv_path))
        if not retry:
            self.log.write("No failed files to retry.")
            return
        # Only keep files that still exist on disk.
        existing = [p for p in retry if Path(p).exists()]
        missing = len(retry) - len(existing)
        self.files = list(dict.fromkeys(Path(p) for p in existing))
        self._update_count()
        msg = f"Queued {len(self.files):,} failed file(s) for retry."
        if missing:
            msg += f" ({missing:,} no longer on disk, skipped.)"
        msg += " Review the destination and press Send."
        self.log.write(msg)

    @staticmethod
    def _read_failures_csv(path: Path) -> list:
        out = []
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


class QueryMovePanel(ToolPanel):
    title = "Query / Move"
    description = ("Query a source node (C-FIND) and pull matching studies to a "
                  "destination AE (C-MOVE).")

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app, label="Source",
                                        remember_key="QueryMovePanel")
        self.picker.grid(row=0, column=0, columnspan=4, sticky="ew",
                         padx=PAD, pady=PAD)

        # Query criteria
        crit = ctk.CTkFrame(self.body, fg_color="transparent")
        crit.grid(row=1, column=0, columnspan=4, sticky="ew", padx=PAD)
        self.fields: dict[str, ctk.CTkEntry] = {}
        for i, (key, label) in enumerate([
            ("PatientID", "Patient ID"),
            ("AccessionNumber", "Accession"),
            ("PatientName", "Patient Name"),
            ("StudyDate", "Study Date (YYYYMMDD)"),
        ]):
            ctk.CTkLabel(crit, text=label, width=150, anchor="w").grid(
                row=i, column=0, sticky="w", pady=2)
            e = ctk.CTkEntry(crit, width=260)
            e.grid(row=i, column=1, sticky="w", pady=2)
            self.fields[key] = e

        self.query_btn = ctk.CTkButton(self.body, text="Query (C-FIND)",
                                       command=self._query)
        self.query_btn.grid(row=2, column=0, padx=PAD, pady=PAD, sticky="w")

        # Move destination row
        movebar = ctk.CTkFrame(self.body, fg_color="transparent")
        movebar.grid(row=3, column=0, columnspan=4, sticky="ew", padx=PAD)
        ctk.CTkLabel(movebar, text="Move to AE:", anchor="w").grid(
            row=0, column=0, padx=(0, PAD))
        self.dest_ae = ctk.CTkEntry(movebar, width=180)
        self.dest_ae.grid(row=0, column=1, sticky="w")
        self.move_btn = ctk.CTkButton(movebar, text="Move selected study",
                                      command=self._move, state="disabled")
        self.move_btn.grid(row=0, column=2, padx=PAD)

        # Results
        self.results = ctk.CTkOptionMenu(self.body, values=["(no results)"],
                                         width=560)
        self.results.grid(row=4, column=0, columnspan=4, sticky="ew",
                          padx=PAD, pady=PAD)
        self._matches = []

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _criteria(self) -> dict[str, str]:
        c = {}
        for key, entry in self.fields.items():
            c[key] = entry.get().strip()
        return c

    def _query(self) -> None:
        node = self.picker.get_node()
        if not node:
            self.log.write("No source selected.")
            return
        self.query_btn.configure(state="disabled")
        self.move_btn.configure(state="disabled")
        my_ae = self.app.settings.my_aetitle
        criteria = self._criteria()
        # Always request identifying return keys.
        ident = scu.build_query("STUDY",
                                StudyInstanceUID="",
                                StudyDescription="",
                                ModalitiesInStudy="",
                                NumberOfStudyRelatedInstances="",
                                **criteria)
        self.log.write("--- C-FIND ---")
        tls = self.app.tls_args_for(node)

        def work():
            return scu.c_find(my_ae, node, ident, model="STUDY",
                              timeout=node.timeout, progress=self.progress,
                              tls_args=tls)

        def done(result):
            self._matches = result.datasets
            if result.datasets:
                labels = []
                for ds in result.datasets:
                    labels.append(
                        f"{getattr(ds, 'PatientName', '?')} | "
                        f"{getattr(ds, 'PatientID', '?')} | "
                        f"{getattr(ds, 'StudyDescription', '')} | "
                        f"{getattr(ds, 'StudyDate', '')}")
                self.results.configure(values=labels)
                self.results.set(labels[0])
                self.move_btn.configure(state="normal")
            else:
                self.results.configure(values=["(no results)"])
                self.results.set("(no results)")
            self.log.write(f"[DONE] {result.message}")
            self.query_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.query_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _move(self) -> None:
        idx = self.results._values.index(self.results.get()) \
            if self.results.get() in self.results._values else -1
        if idx < 0 or idx >= len(self._matches):
            self.log.write("Select a study to move.")
            return
        dest = self.dest_ae.get().strip()
        if not dest:
            self.log.write("Enter a Move-to AE title.")
            return
        node = self.picker.get_node()
        my_ae = self.app.settings.my_aetitle
        ds = self._matches[idx]
        ident = scu.build_query("STUDY",
                                StudyInstanceUID=str(ds.StudyInstanceUID))
        self.move_btn.configure(state="disabled")
        self.log.write(f"--- C-MOVE study to {dest} ---")
        tls = self.app.tls_args_for(node)

        def work():
            return scu.c_move(my_ae, node, dest, ident, model="STUDY",
                              timeout=max(node.timeout, 120),
                              progress=self.progress, tls_args=tls)

        def done(result):
            self.log.write(f"[DONE] {result.message}")
            self.move_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.move_btn.configure(state="normal")

        run_threaded(self, work, done, err)


class RetrievePanel(ToolPanel):
    title = "Retrieve (C-Get)"
    description = ("Query a source, then pull matching studies straight to a "
                  "local folder over one association (firewall-friendly).")

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app, label="Source",
                                        remember_key="RetrievePanel")
        self.picker.grid(row=0, column=0, columnspan=4, sticky="ew",
                         padx=PAD, pady=PAD)

        crit = ctk.CTkFrame(self.body, fg_color="transparent")
        crit.grid(row=1, column=0, columnspan=4, sticky="ew", padx=PAD)
        self.fields: dict[str, ctk.CTkEntry] = {}
        for i, (key, label) in enumerate([
            ("PatientID", "Patient ID"),
            ("AccessionNumber", "Accession"),
            ("StudyDate", "Study Date (YYYYMMDD)"),
        ]):
            ctk.CTkLabel(crit, text=label, width=150, anchor="w").grid(
                row=i, column=0, sticky="w", pady=2)
            e = ctk.CTkEntry(crit, width=260)
            e.grid(row=i, column=1, sticky="w", pady=2)
            self.fields[key] = e

        self.query_btn = ctk.CTkButton(self.body, text="Query (C-FIND)",
                                       command=self._query)
        self.query_btn.grid(row=2, column=0, padx=PAD, pady=PAD, sticky="w")

        outbar = ctk.CTkFrame(self.body, fg_color="transparent")
        outbar.grid(row=3, column=0, columnspan=4, sticky="ew", padx=PAD)
        ctk.CTkButton(outbar, text="Output folder...",
                      command=self._pick_out).pack(side="left")
        self.out_lbl = ctk.CTkLabel(outbar, text="(not set)",
                                    text_color=MUTED)
        self.out_lbl.pack(side="left", padx=PAD)
        self.retrieve_btn = ctk.CTkButton(outbar, text="Retrieve selected",
                                          command=self._retrieve,
                                          state="disabled")
        self.retrieve_btn.pack(side="left", padx=PAD)

        self.results = ctk.CTkOptionMenu(self.body, values=["(no results)"],
                                         width=560)
        self.results.grid(row=4, column=0, columnspan=4, sticky="ew",
                          padx=PAD, pady=PAD)
        self._matches = []
        self.out_dir: Path | None = None

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=folder)

    def _query(self) -> None:
        node = self.picker.get_node()
        if not node:
            self.log.write("No source selected.")
            return
        self.query_btn.configure(state="disabled")
        self.retrieve_btn.configure(state="disabled")
        my_ae = self.app.settings.my_aetitle
        criteria = {k: e.get().strip() for k, e in self.fields.items()}
        ident = scu.build_query("STUDY", StudyInstanceUID="",
                                StudyDescription="", PatientName="",
                                **criteria)
        self.log.write("--- C-FIND ---")
        tls = self.app.tls_args_for(node)

        def work():
            return scu.c_find(my_ae, node, ident, model="STUDY",
                              timeout=node.timeout, progress=self.progress,
                              tls_args=tls)

        def done(result):
            self._matches = result.datasets
            if result.datasets:
                labels = [f"{getattr(d,'PatientName','?')} | "
                          f"{getattr(d,'PatientID','?')} | "
                          f"{getattr(d,'StudyDescription','')} | "
                          f"{getattr(d,'StudyDate','')}" for d in result.datasets]
                self.results.configure(values=labels)
                self.results.set(labels[0])
                self.retrieve_btn.configure(state="normal")
            else:
                self.results.configure(values=["(no results)"])
                self.results.set("(no results)")
            self.log.write(f"[DONE] {result.message}")
            self.query_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.query_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _retrieve(self) -> None:
        vals = self.results._values
        cur = self.results.get()
        idx = vals.index(cur) if cur in vals else -1
        if idx < 0 or idx >= len(self._matches):
            self.log.write("Select a study to retrieve.")
            return
        if not self.out_dir:
            self.log.write("Choose an output folder first.")
            return
        node = self.picker.get_node()
        my_ae = self.app.settings.my_aetitle
        ds = self._matches[idx]
        ident = scu.build_query("STUDY",
                                StudyInstanceUID=str(ds.StudyInstanceUID))
        out = self.out_dir
        self.retrieve_btn.configure(state="disabled")
        self.log.write(f"--- C-GET -> {out} ---")
        tls = self.app.tls_args_for(node)

        def work():
            return scu.c_get(my_ae, node, ident, out, model="STUDY",
                             timeout=max(node.timeout, 120),
                             progress=self.progress, tls_args=tls)

        def done(result):
            self.log.write(f"[DONE] {result.message}")
            self.retrieve_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.retrieve_btn.configure(state="normal")

        run_threaded(self, work, done, err)


class StorageCommitPanel(ToolPanel):
    title = "Storage Commit"
    description = ("Ask an archive to confirm it has safely stored specific "
                  "objects (adds DICOM files to read their SOP UIDs).")

    def build(self) -> None:
        self.files: list[Path] = []
        self.picker = DestinationPicker(self.body, self.app, label="Archive",
                                        remember_key="StorageCommitPanel")
        self.picker.grid(row=0, column=0, columnspan=4, sticky="ew",
                         padx=PAD, pady=PAD)

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=4, sticky="ew", padx=PAD)
        ctk.CTkButton(bar, text="Add Files...",
                      command=self._add_files).pack(side="left")
        ctk.CTkButton(bar, text="Add Folder...",
                      command=self._add_folder).pack(side="left", padx=PAD)
        self.count_lbl = ctk.CTkLabel(bar, text="No files.", text_color=MUTED)
        self.count_lbl.pack(side="left", padx=PAD)

        portbar = ctk.CTkFrame(self.body, fg_color="transparent")
        portbar.grid(row=2, column=0, columnspan=4, sticky="ew", padx=PAD,
                     pady=PAD)
        ctk.CTkLabel(portbar, text="Listen port (for result report):").pack(
            side="left")
        self.port = ctk.CTkEntry(portbar, width=90)
        self.port.insert(0, "11115")
        self.port.pack(side="left", padx=PAD)
        self.btn = ctk.CTkButton(portbar, text="Request Commitment",
                                 command=self._run)
        self.btn.pack(side="left", padx=PAD)

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in paths)
        self._update_count()

    def _add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.files.extend(find_dicom_files(Path(folder)))
        self._update_count()

    def _update_count(self):
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files)} file(s).")

    def _run(self):
        node = self.picker.get_node()
        if not node:
            self.log.write("No archive selected.")
            return
        if not self.files:
            self.log.write("Add the DICOM files whose storage you want "
                           "confirmed.")
            return
        try:
            port = int(self.port.get().strip())
        except ValueError:
            port = 11115
        my_ae = self.app.settings.my_aetitle
        files = list(self.files)
        self.btn.configure(state="disabled")
        self.log.write("--- Reading SOP UIDs ---")

        def work():
            refs = []
            for f in files:
                try:
                    ds = dcmread(str(f), force=True, stop_before_pixels=True)
                    refs.append((str(ds.SOPClassUID), str(ds.SOPInstanceUID)))
                except Exception:  # noqa: BLE001
                    pass
            refs = list(dict.fromkeys(refs))
            self.progress(f"Requesting commitment for {len(refs)} instance(s).")
            return scu.storage_commit(my_ae, node, refs, listen_port=port,
                                      timeout=60, progress=self.progress)

        def done(result):
            self.log.write(f"[DONE] {result.message}")
            if result.failed:
                self.log.write(f"   Failed instances: {len(result.failed)}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)
