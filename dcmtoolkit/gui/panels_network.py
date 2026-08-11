"""Network tool panels: Echo, Send, Query/Move."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..net import scu
from ..tools.fileops import find_dicom_files
from .base import ToolPanel
from .widgets import DestinationPicker, run_threaded, section, PAD


class EchoPanel(ToolPanel):
    title = "C-Echo"
    description = "Verify connectivity to a remote DICOM node (DICOM ping)."

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app)
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

        def work():
            return scu.c_echo(my_ae, node, progress=self.progress)

        def done(result):
            mark = "OK" if result.success else "FAIL"
            self.log.write(f"[{mark}] {result.message}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)


class SendPanel(ToolPanel):
    title = "Send (C-Store)"
    description = "Send DICOM files or a folder to a destination."

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app)
        self.picker.grid(row=0, column=0, columnspan=3, sticky="ew",
                         padx=PAD, pady=PAD)

        self.files: list[Path] = []
        self.count_lbl = ctk.CTkLabel(self.body, text="No files selected.",
                                      anchor="w")
        self.count_lbl.grid(row=1, column=0, columnspan=3, sticky="w",
                            padx=PAD)

        ctk.CTkButton(self.body, text="Add Files...",
                      command=self._add_files).grid(
            row=2, column=0, padx=PAD, pady=PAD, sticky="w")
        ctk.CTkButton(self.body, text="Add Folder...",
                      command=self._add_folder).grid(
            row=2, column=1, padx=0, pady=PAD, sticky="w")
        ctk.CTkButton(self.body, text="Clear",
                      command=self._clear, width=70).grid(
            row=2, column=2, padx=PAD, pady=PAD, sticky="w")

        self.btn = ctk.CTkButton(self.body, text="Send", command=self._run)
        self.btn.grid(row=3, column=0, sticky="w", padx=PAD, pady=(0, PAD))

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select DICOM files",
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in paths)
        self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder of DICOM files")
        if folder:
            found = find_dicom_files(Path(folder))
            self.files.extend(found)
            self.log.write(f"Found {len(found)} file(s) in {folder}")
        self._update_count()

    def _clear(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        # de-dupe while preserving order
        seen = set()
        uniq = []
        for f in self.files:
            if f not in seen:
                seen.add(f)
                uniq.append(f)
        self.files = uniq
        self.count_lbl.configure(text=f"{len(self.files)} file(s) queued.")

    def _run(self) -> None:
        node = self.picker.get_node()
        if not node:
            self.log.write("No destination selected.")
            return
        if not self.files:
            self.log.write("No files queued.")
            return
        self.btn.configure(state="disabled")
        self.log.write(f"--- Sending {len(self.files)} file(s) to "
                       f"{node.name} ---")
        my_ae = self.app.settings.my_aetitle
        files = list(self.files)

        def work():
            return scu.c_store(my_ae, node, files, progress=self.progress)

        def done(result):
            self.log.write(f"[DONE] sent={result.sent} failed={result.failed} "
                           f"warnings={result.warnings}")
            for e in result.errors[:20]:
                self.log.write(f"   {e}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)


class QueryMovePanel(ToolPanel):
    title = "Query / Move"
    description = ("Query a source node (C-FIND) and pull matching studies to a "
                  "destination AE (C-MOVE).")

    def build(self) -> None:
        self.picker = DestinationPicker(self.body, self.app, label="Source")
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

        def work():
            return scu.c_find(my_ae, node, ident, model="STUDY",
                              progress=self.progress)

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

        def work():
            return scu.c_move(my_ae, node, dest, ident, model="STUDY",
                              progress=self.progress)

        def done(result):
            self.log.write(f"[DONE] {result.message}")
            self.move_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.move_btn.configure(state="normal")

        run_threaded(self, work, done, err)
