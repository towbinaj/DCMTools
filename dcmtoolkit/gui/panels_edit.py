"""Patient-level and Study-level demographic editors.

Load a folder/study, show the demographic fields in labeled inputs, validate
edits against the DICOM VR (date format, length, charset), and write the
changes across every file using the shared modify engine (so it gets parallel
workers, live progress, per-folder report and retry).
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
from pydicom import dcmread
from pydicom.datadict import tag_for_keyword
from pydicom.tag import Tag

from .. import config
from ..tools import dicomtags, fileops
from .base import ToolPanel
from .batch import BatchRunner
from .panels_files import _DropSourceMixin, _folder_totals
from .theme import MUTED
from .widgets import run_threaded, PAD


class _DemographicEditor(_DropSourceMixin, ToolPanel):
    # Subclasses set these:
    FIELDS: list[tuple[str, str, str]] = []   # (keyword, label, VR)
    entity = "record"
    level_key = "PatientID"

    def build(self) -> None:
        self.files: list[Path] = []
        self._orig: dict[str, str] = {}
        self._field_widgets: list = []

        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(5, weight=1)

        self._make_source("Save").grid(row=0, column=0, sticky="ew",
                                       padx=PAD, pady=PAD)

        self.status = ctk.CTkLabel(self.body, text="Add a folder or files to "
                                   "load.", anchor="w", text_color=MUTED)
        self.status.grid(row=1, column=0, sticky="w", padx=PAD)

        form = ctk.CTkFrame(self.body)
        form.grid(row=2, column=0, sticky="ew", padx=PAD, pady=PAD)
        form.grid_columnconfigure(1, weight=1)
        for i, (kw, label, vr) in enumerate(self.FIELDS):
            ctk.CTkLabel(form, text=label, anchor="w", width=180).grid(
                row=i, column=0, sticky="w", padx=PAD, pady=4)
            entry = ctk.CTkEntry(form, width=320)
            entry.grid(row=i, column=1, sticky="ew", padx=PAD, pady=4)
            vlabel = ctk.CTkLabel(form, text="", anchor="w", text_color=MUTED)
            vlabel.grid(row=i, column=2, sticky="w", padx=PAD, pady=4)
            entry.bind("<KeyRelease>",
                       lambda _e, k=kw: self._validate_field(k))
            self._field_widgets.append((kw, label, vr, entry, vlabel))

        self.in_place = ctk.CTkCheckBox(
            self.body,
            text="Overwrite files in place (else write to ./modified)")
        self.in_place.grid(row=3, column=0, sticky="w", padx=PAD, pady=PAD)

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=4, column=0, sticky="w", padx=PAD, pady=(0, PAD))
        self.btn = ctk.CTkButton(btnbar, text="Save changes",
                                 command=self._save)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btnbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)
        self.reload_btn = ctk.CTkButton(btnbar, text="Reload", width=80,
                                        command=self._load_demographics)
        self.reload_btn.pack(side="left", padx=PAD)
        self._workers_control(btnbar)

        self.runner = BatchRunner(self, verb="changed")
        self.runner.build(self.body).grid(row=5, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))

    # -- loading ---------------------------------------------------------
    def _update_count(self):  # override: also (re)load demographics
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")
        self._load_demographics()

    def _load_demographics(self):
        if not self.files:
            return
        first = self.files[0]
        try:
            ds = dcmread(str(first), force=True, stop_before_pixels=True)
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text=f"Could not read {first.name}: {exc}",
                                  text_color="#d9695f")
            return
        for kw, _label, _vr, entry, _vlabel in self._field_widgets:
            val = str(ds.get(kw, "") or "")
            entry.delete(0, "end")
            entry.insert(0, val)
            self._orig[kw] = val
            self._validate_field(kw)

        # Warn if the loaded files span more than one patient/study.
        key = self.level_key
        files = list(self.files)

        def work():
            vals = set()
            for f in files:
                try:
                    d = dcmread(str(f), force=True, stop_before_pixels=True)
                    vals.add(str(d.get(key, "")))
                except Exception:  # noqa: BLE001
                    pass
            return vals

        def done(vals):
            n = len(vals)
            if n > 1:
                self.status.configure(
                    text=f"! {len(files):,} files span {n} different "
                         f"{self.entity} values — saving sets them ALL to the "
                         f"values below.", text_color="#d9a441")
            else:
                self.status.configure(
                    text=f"{len(files):,} file(s), one {self.entity}. "
                         f"Edit fields and Save.", text_color=MUTED)

        run_threaded(self, work, done)

    # -- validation ------------------------------------------------------
    def _validate_field(self, kw: str) -> bool:
        for k, _label, vr, entry, vlabel in self._field_widgets:
            if k == kw:
                ok, msg = dicomtags.validate(vr, entry.get())
                vlabel.configure(text=("OK" if ok and entry.get() else msg),
                                 text_color=(MUTED if ok else "#d9695f"))
                return ok
        return True

    def _validate_all(self) -> bool:
        return all(self._validate_field(kw)
                   for kw, *_ in self._field_widgets)

    # -- save ------------------------------------------------------------
    def requeue(self, requeue_paths):  # after a retry, reload too
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

    def _save(self):
        if not self.files:
            self.log.write("Add a folder or files first.")
            return
        if not self._validate_all():
            self.log.write("Fix the invalid field(s) shown in red first.")
            return
        ops = []
        for kw, _label, _vr, entry, _vlabel in self._field_widgets:
            newv = entry.get()
            if newv != self._orig.get(kw, ""):
                t = Tag(tag_for_keyword(kw))
                ops.append(fileops.ModifyOp(tag=dicomtags.to_hex(t),
                                            action="set", value=newv))
        if not ops:
            self.log.write("No changes to save.")
            return
        files = list(self.files)
        total = len(files)
        in_place = bool(self.in_place.get())
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(
            f"--- Writing {len(ops)} field(s) to {total:,} file(s)"
            f"{' in place' if in_place else ' -> ./modified subfolders'} ---")

        def worker():
            return fileops.modify_files(
                files, ops, in_place=in_place,
                progress=self.progress, on_item=self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled,
                workers=self._get_workers())

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            # refresh originals so a second save only writes new changes
            for kw, _l, _vr, entry, _vl in self._field_widgets:
                self._orig[kw] = entry.get()

        self.runner.run(total, "edit", worker, on_done=on_done,
                        folder_totals=_folder_totals(files))


class PatientEditPanel(_DemographicEditor):
    title = "Patient Info"
    description = ("Edit patient-level demographics across all files, with "
                  "DICOM validation.")
    entity = "patient"
    level_key = "PatientID"
    FIELDS = [
        ("PatientName", "Patient Name", "PN"),
        ("PatientID", "Patient ID", "LO"),
        ("PatientBirthDate", "Birth Date (YYYYMMDD)", "DA"),
        ("PatientSex", "Sex (M / F / O)", "CS"),
        ("PatientAge", "Age (e.g. 045Y)", "AS"),
        ("PatientBirthTime", "Birth Time (HHMMSS)", "TM"),
        ("OtherPatientIDs", "Other Patient IDs", "LO"),
    ]


class StudyEditPanel(_DemographicEditor):
    title = "Study Info"
    description = ("Edit study-level demographics across all files, with "
                  "DICOM validation.")
    entity = "study"
    level_key = "StudyInstanceUID"
    FIELDS = [
        ("StudyDate", "Study Date (YYYYMMDD)", "DA"),
        ("StudyTime", "Study Time (HHMMSS)", "TM"),
        ("AccessionNumber", "Accession Number", "SH"),
        ("StudyID", "Study ID", "SH"),
        ("StudyDescription", "Study Description", "LO"),
        ("ReferringPhysicianName", "Referring Physician", "PN"),
    ]


class SeriesEditPanel(_DemographicEditor):
    title = "Series Info"
    description = ("Edit series-level attributes across all files, with "
                  "DICOM validation.")
    entity = "series"
    level_key = "SeriesInstanceUID"
    FIELDS = [
        ("SeriesNumber", "Series Number", "IS"),
        ("SeriesDescription", "Series Description", "LO"),
        ("SeriesDate", "Series Date (YYYYMMDD)", "DA"),
        ("SeriesTime", "Series Time (HHMMSS)", "TM"),
        ("Modality", "Modality", "CS"),
        ("BodyPartExamined", "Body Part Examined", "CS"),
        ("ProtocolName", "Protocol Name", "LO"),
        ("Laterality", "Laterality (L / R)", "CS"),
    ]
