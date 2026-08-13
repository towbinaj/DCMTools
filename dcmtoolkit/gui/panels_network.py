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

from .. import config, paths
from ..net import scu
from ..tools.fileops import find_dicom_files
from .base import ToolPanel
from .batch import BatchRunner
from .theme import MUTED
from .widgets import (DestinationPicker, build_drop_zone, run_threaded,
                      section, PAD)


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
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=90)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(5, weight=1)

        self.picker = DestinationPicker(self.body, self.app,
                                        remember_key="SendPanel")
        self.picker.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)

        self.files: list[Path] = []
        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "Send",
            [("Add Files...", self._add_files, 110),
             ("Add Folder...", self._add_folder, 120),
             ("Clear", self._clear, 70)])
        zone.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)

        runbar = ctk.CTkFrame(self.body, fg_color="transparent")
        runbar.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        self.btn = ctk.CTkButton(runbar, text="Send", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(runbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)
        ctk.CTkLabel(runbar, text="Parallel").pack(side="left", padx=(PAD, 4))
        self.workers = ctk.CTkOptionMenu(
            runbar, values=["1", "2", "4", "6", "8"], width=64)
        self.workers.set(str(self.app.settings.send_workers))
        self.workers.pack(side="left")

        self.runner = BatchRunner(self, verb="sent")
        self.runner.build(self.body).grid(row=5, column=0, sticky="nsew",
                                          padx=PAD, pady=PAD)

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _cancel(self) -> None:
        self.runner.cancel()

    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Select DICOM files",
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in chosen)
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

    def _on_drop(self, dropped: list) -> None:
        chosen = [Path(p) for p in dropped]
        loose = [p for p in chosen if p.is_file()]
        self.files.extend(loose)
        folders = [p for p in chosen if p.is_dir()]
        if loose:
            self.log.write(f"Added {len(loose):,} dropped file(s).")
        if folders:
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                # Return per-folder file lists so we can report each one.
                return [(d, find_dicom_files(d)) for d in folders]

            def done(results):
                for d, found in results:
                    self.files.extend(found)
                    self.log.write(f"  + {d.name}:  {len(found):,} file(s)")
                self._update_count()

            run_threaded(self, work, done)
        else:
            self._update_count()

    def _clear(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s) queued.")

    def requeue(self, requeue_paths: list) -> None:
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

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
        my_ae = self.app.settings.my_aetitle
        files = list(self.files)
        total = len(files)
        tls = self.app.tls_args_for(node)
        try:
            workers = int(self.workers.get())
        except ValueError:
            workers = 4
        # Persist the chosen parallelism.
        if self.app.settings.send_workers != workers:
            self.app.settings.send_workers = workers
            config.save_settings(self.app.settings)
        # Per-folder totals so folder completion is accurate with parallel sends.
        folder_totals = {}
        for f in files:
            k = str(f.parent)
            folder_totals[k] = folder_totals.get(k, 0) + 1
        self.log.write(f"--- Sending {total:,} file(s) in "
                       f"{len(folder_totals)} folder(s) to {node.name} "
                       f"using {workers} parallel association(s) ---")

        def worker():
            return scu.c_store(my_ae, node, files, progress=self.progress,
                               should_cancel=lambda: self.runner.cancelled,
                               timeout=node.timeout, tls_args=tls,
                               on_file=self.runner.on_item, workers=workers)

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")

        self.runner.run(total, "send", worker, on_done=on_done,
                        folder_totals=folder_totals)


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

        self.retr_progress = ctk.CTkProgressBar(self.body)
        self.retr_progress.set(0)
        self.retr_progress.grid(row=5, column=0, columnspan=4, sticky="ew",
                                padx=PAD, pady=(PAD, 2))
        self.retr_status = ctk.CTkLabel(
            self.body, text="", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.retr_status.grid(row=6, column=0, columnspan=4, sticky="w",
                              padx=PAD)
        self._matches = []
        self.out_dir: Path | None = None
        self._retr_state = {"n": 0, "remaining": 0, "last_ui": 0.0}

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
        self.retr_progress.set(0)
        self.retr_status.configure(text="Starting retrieve...")
        self.log.write(f"--- C-GET -> {out} ---")
        tls = self.app.tls_args_for(node)

        def on_object(n, remaining):
            # Called on the network thread; throttle UI updates to ~4/sec.
            now = time.monotonic()
            self._retr_state["n"] = n
            self._retr_state["remaining"] = remaining
            if now - self._retr_state["last_ui"] < 0.25:
                return
            self._retr_state["last_ui"] = now
            total = n + remaining
            frac = (n / total) if total else 0

            def apply():
                self.retr_progress.set(frac)
                extra = f" of ~{total:,}" if total else ""
                self.retr_status.configure(
                    text=f"Received {n:,}{extra} object(s)...")
            self.after(0, apply)

        def work():
            return scu.c_get(my_ae, node, ident, out, model="STUDY",
                             timeout=max(node.timeout, 120),
                             progress=self.progress, tls_args=tls,
                             on_object=on_object)

        def done(result):
            self.retr_progress.set(1.0)
            self.retr_status.configure(
                text=f"Done. Saved {result.saved:,} object(s), "
                     f"failed {result.failed}.")
            self.log.write(f"[DONE] {result.message}")
            if result.saved_files:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                rep = paths.reports_dir() / f"retrieve_{stamp}_files.csv"
                try:
                    with open(rep, "w", newline="", encoding="utf-8") as fh:
                        w = csv.writer(fh)
                        w.writerow(["saved_file"])
                        for p in result.saved_files:
                            w.writerow([p])
                    self.log.write(f"Saved list of {len(result.saved_files):,} "
                                   f"received file(s) to {rep}")
                except OSError:
                    pass
            self.retrieve_btn.configure(state="normal")

        def err(exc, tb):
            self.retr_status.configure(text="Retrieve failed.")
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
        ctk.CTkButton(bar, text="Clear", width=70,
                      command=self._clear).pack(side="left", padx=PAD)
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

    def _clear(self):
        self.files = []
        self._update_count()

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
