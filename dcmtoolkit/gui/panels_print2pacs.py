"""Print2PACS panel: import images / PDF / video / DICOM objects as a new
series on an existing PACS study.

Ported from the standalone Print2PACS app. The GUI-free engine lives in
``dcmtoolkit.print2pacs`` (sc_import / video / dicom_import / reconcile / send);
this panel is the toolkit-style front end, reusing the shared DestinationPicker,
drop zone, threaded-run helper, log box, and the StepWizard.

Three steps: (1) drop items; (2) look up the study by accession / patient ID on
a query PACS (C-FIND) and confirm it; (3) set the new series details and send to
a storage PACS (C-STORE). Images/PDF become Secondary Capture; video becomes
encapsulated H.264 (needs ffmpeg); a dropped DICOM object is re-filed onto the
study keeping its native modality.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..model import (StudyContext, SeriesInfo, BODY_PARTS_EXAMINED,
                     CONVERSION_TYPES, DEST_GROUP_STORAGE)
from ..net import scu
from ..print2pacs import (sc_import, video, dicom_import, ffmpeg_tools,
                          reconcile, send)
from ..tools import render
from .base import ToolPanel
from .theme import MUTED
from .widgets import (DestinationPicker, StepWizard, build_drop_zone,
                      run_threaded, PAD)

AUTO_MODALITY = "Auto (by type)"
MODALITY_CHOICES = [AUTO_MODALITY, "OT", "XC", "SC", "OPT", "OP", "ES",
                    "GM", "SM", "US", "XA"]
PDF_DPI = 200
SERIES_NUMBER = 9901
_ERR = "#d9695f"


class Print2PACSPanel(ToolPanel):
    title = "Print2PACS"
    description = ("Send images, PDFs, video, or existing DICOM objects to PACS "
                   "as a new series on an existing study, in three steps.")

    # -- build -----------------------------------------------------------
    def build(self) -> None:
        self.pages: list[sc_import.SourcePage] = []
        self.videos: list = []          # (Path, PIL thumbnail or None)
        self.dicoms: list = []          # (DicomSource, PIL thumbnail or None)
        self._thumb_imgs: list = []     # keep CTkImage refs alive (GC guard)
        self.studies: list[StudyContext] = []
        self.chosen_study: StudyContext | None = None
        self._busy = False

        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.wiz = StepWizard(
            self.body, ["Select items", "Find the study", "Review & send"],
            on_finish=self._send, finish_text="Send to PACS",
            validate=self._validate_step)
        self.wiz.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(PAD, 2))

        self.pbar = ctk.CTkProgressBar(self.body)
        self.pbar.set(0)
        self.pbar.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))

        self._build_items(self.wiz.step(0))
        self._build_study(self.wiz.step(1))
        self._build_send(self.wiz.step(2))
        self._refresh_items()

    def _build_items(self, parent) -> None:
        parent.grid_rowconfigure(1, weight=1)
        build_drop_zone(
            self.app, parent, self._on_drop, "Add",
            [("Add files...", self._add_files, 120),
             ("Clear", self._clear, 70)])[0].grid(
            row=0, column=0, sticky="ew", pady=(0, 2))
        self.items = ctk.CTkScrollableFrame(parent, label_text="Items to send")
        self.items.grid(row=1, column=0, sticky="nsew", pady=2)
        self.items.grid_columnconfigure(0, weight=1)
        self.count_lbl = ctk.CTkLabel(parent, text="No items.",
                                      text_color=MUTED, anchor="w")
        self.count_lbl.grid(row=2, column=0, sticky="w")

    def _build_study(self, parent) -> None:
        parent.grid_columnconfigure(1, weight=1)
        self.query_pick = DestinationPicker(
            parent, self.app, label="Query PACS",
            remember_key="print2pacs_query", groups=(DEST_GROUP_STORAGE,))
        self.query_pick.grid(row=0, column=0, columnspan=4, sticky="ew",
                             pady=(0, 6))
        ctk.CTkLabel(parent, text="Accession #", width=90, anchor="w").grid(
            row=1, column=0, sticky="w", pady=2)
        self.acc_entry = ctk.CTkEntry(parent, width=200)
        self.acc_entry.grid(row=1, column=1, sticky="w", pady=2)
        ctk.CTkLabel(parent, text="or Patient ID").grid(row=1, column=2, padx=PAD)
        self.pid_entry = ctk.CTkEntry(parent, width=160)
        self.pid_entry.grid(row=1, column=3, sticky="w")
        bb = ctk.CTkFrame(parent, fg_color="transparent")
        bb.grid(row=2, column=0, columnspan=4, sticky="w", pady=6)
        self.lookup_btn = ctk.CTkButton(bb, text="Look up study",
                                        command=self._lookup, width=130)
        self.lookup_btn.pack(side="left")
        ctk.CTkButton(bb, text="Test (C-ECHO)", command=self._test, width=120,
                      fg_color="transparent", border_width=1).pack(
            side="left", padx=PAD)
        self.study_menu = ctk.CTkOptionMenu(parent, values=["(look up a study)"],
                                            command=self._pick_study, width=560)
        self.study_menu.grid(row=3, column=0, columnspan=4, sticky="ew",
                             pady=(2, 2))
        self.study_lbl = ctk.CTkLabel(parent, text="No study selected.",
                                      text_color=MUTED, anchor="w",
                                      justify="left")
        self.study_lbl.grid(row=4, column=0, columnspan=4, sticky="w")

    def _build_send(self, parent) -> None:
        parent.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(parent, text="Modality", width=90, anchor="w").grid(
            row=0, column=0, pady=4, sticky="w")
        self.modality = ctk.CTkOptionMenu(parent, values=MODALITY_CHOICES,
                                          width=160)
        self.modality.grid(row=0, column=1, sticky="w", pady=4)
        ctk.CTkLabel(parent, text="Conversion").grid(row=0, column=2, padx=PAD)
        self.conv = ctk.CTkOptionMenu(
            parent, width=200,
            values=[f"{k} - {v}" for k, v in CONVERSION_TYPES.items()])
        self.conv.set("WSD - Workstation")
        self.conv.grid(row=0, column=3, sticky="w", pady=4)
        ctk.CTkLabel(parent, text="Description", width=90, anchor="w").grid(
            row=1, column=0, pady=4, sticky="w")
        self.desc = ctk.CTkEntry(parent)
        self.desc.insert(0, "Print2PACS Import")
        self.desc.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ctk.CTkLabel(parent, text="Body part", width=90, anchor="w").grid(
            row=2, column=0, pady=4, sticky="w")
        self.body_part = ctk.CTkComboBox(parent, values=[""] + BODY_PARTS_EXAMINED,
                                         width=200)
        self.body_part.set("")
        self.body_part.grid(row=2, column=1, sticky="w", pady=4)
        self.strip_private = ctk.CTkCheckBox(
            parent, text="Strip private tags (dropped DICOM)")
        self.strip_private.grid(row=2, column=2, columnspan=2, sticky="w")
        self.keep_copy = ctk.CTkCheckBox(
            parent, text="Keep a local copy of what's sent")
        self.keep_copy.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.send_pick = DestinationPicker(
            parent, self.app, label="Send PACS",
            remember_key="print2pacs_send", groups=(DEST_GROUP_STORAGE,))
        self.send_pick.grid(row=4, column=0, columnspan=4, sticky="ew",
                           pady=(PAD, 0))

    def on_destinations_changed(self) -> None:
        for p in (getattr(self, "query_pick", None),
                  getattr(self, "send_pick", None)):
            if p:
                p.refresh()

    def _validate_step(self, i: int):
        if i == 0 and not (self.pages or self.videos or self.dicoms):
            return False, "Add at least one item to send."
        if i == 1 and not self.chosen_study:
            return False, "Look up and select a study first."
        return True, ""

    # -- items -----------------------------------------------------------
    @staticmethod
    def _accepted(p) -> bool:
        return (sc_import.is_supported(p) or video.is_video(p)
                or dicom_import.is_dicom(p))

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Select images, PDF, video, or DICOM",
            filetypes=[("Images / PDF", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff "
                        "*.pdf"),
                       ("Video", "*.mp4 *.mov *.avi *.mkv *.m4v *.wmv"),
                       ("DICOM", "*.dcm *.dicom *.ima"), ("All files", "*.*")])
        if files:
            self._ingest(list(files))

    def _on_drop(self, paths_list):
        self._ingest(paths_list)

    def _ingest(self, file_paths):
        accepted = [p for p in file_paths if self._accepted(p)]
        skipped = len(file_paths) - len(accepted)
        if not accepted:
            self.log.write("No supported files. Accepted: images (JPG/PNG/TIFF/"
                           "BMP/PDF), video (MP4/MOV/AVI/MKV), DICOM (.dcm).")
            return
        img = [p for p in accepted if sc_import.is_supported(p)]
        vid = [p for p in accepted
               if not sc_import.is_supported(p) and video.is_video(p)]
        dcm = [p for p in accepted
               if not sc_import.is_supported(p) and not video.is_video(p)
               and dicom_import.is_dicom(p)]
        self.count_lbl.configure(text="Loading...")

        def work():
            pages = sc_import.load_many(img, PDF_DPI)
            vids = [(Path(v), self._video_thumb(v)) for v in vid]
            dcms = [(dicom_import.load_dicom(d), self._dicom_thumb(d))
                    for d in dcm]
            return pages, vids, dcms

        def done(result):
            pages, vids, dcms = result
            self.pages.extend(pages)
            self.videos.extend(vids)
            self.dicoms.extend(dcms)
            self._refresh_items()
            if skipped:
                self.log.write(f"Skipped {skipped} unsupported file(s).")

        def err(exc):
            self.log.write(f"[ERROR] Could not load files: {exc}")
            self._refresh_items()

        run_threaded(self, work, done, err)

    def _clear(self):
        self.pages, self.videos, self.dicoms = [], [], []
        self._thumb_imgs = []
        self._refresh_items()

    @staticmethod
    def _video_thumb(path):
        """First video frame as a PIL thumbnail (needs ffmpeg); None if not."""
        try:
            if ffmpeg_tools.have_ffmpeg():
                return video.first_frame(path)
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _dicom_thumb(path):
        """Render a dropped DICOM's first frame to a PIL image; None on any
        failure (non-image object, missing decoder, ...)."""
        try:
            from pydicom import dcmread
            ds = dcmread(str(path), force=True)
            return render.render_image(ds, 0)
        except Exception:  # noqa: BLE001
            return None

    def _thumb_widget(self, parent, pil):
        """A 64px thumbnail label for a PIL image, or a 'no preview' placeholder.
        Keeps the CTkImage referenced so Tk doesn't garbage-collect it."""
        if pil is not None:
            try:
                t = pil.copy()
                t.thumbnail((64, 64))
                img = ctk.CTkImage(light_image=t, dark_image=t, size=t.size)
                self._thumb_imgs.append(img)
                return ctk.CTkLabel(parent, image=img, text="")
            except Exception:  # noqa: BLE001
                pass
        return ctk.CTkLabel(parent, text="no\npreview", width=64, height=44,
                            text_color=MUTED, font=ctk.CTkFont(size=10))

    def _refresh_items(self):
        for w in self.items.winfo_children():
            w.destroy()
        self._thumb_imgs = []
        items = []
        for i, p in enumerate(self.pages):
            nm = Path(p.source).name + (f"  (page {p.page_index + 1})"
                                        if p.page_index else "")
            items.append(("Image/PDF", nm, p.image, ("page", i)))
        for i, (v, thumb) in enumerate(self.videos):
            items.append(("Video", Path(v).name, thumb, ("video", i)))
        for i, (s, thumb) in enumerate(self.dicoms):
            items.append(("DICOM (re-file)", s.label, thumb, ("dicom", i)))
        for row, (kind, name, pil, ref) in enumerate(items):
            fr = ctk.CTkFrame(self.items)
            fr.grid(row=row, column=0, sticky="ew", pady=2, padx=2)
            fr.grid_columnconfigure(2, weight=1)
            self._thumb_widget(fr, pil).grid(row=0, column=0, padx=6, pady=4)
            ctk.CTkLabel(fr, text=kind, width=110, anchor="w",
                         text_color=MUTED).grid(row=0, column=1, padx=(0, PAD))
            ctk.CTkLabel(fr, text=str(name), anchor="w").grid(
                row=0, column=2, sticky="w")
            ctk.CTkButton(fr, text="Remove", width=70, fg_color="transparent",
                          border_width=1,
                          command=lambda r=ref: self._remove(r)).grid(
                row=0, column=3, padx=PAD)
        n = len(self.pages) + len(self.videos) + len(self.dicoms)
        self.count_lbl.configure(
            text="No items." if not n else
            f"{n} item(s): {len(self.pages)} image/PDF, "
            f"{len(self.videos)} video, {len(self.dicoms)} DICOM.")

    def _remove(self, ref):
        kind, i = ref
        seq = {"page": self.pages, "video": self.videos,
               "dicom": self.dicoms}[kind]
        if 0 <= i < len(seq):
            seq.pop(i)
        self._refresh_items()

    # -- study lookup ----------------------------------------------------
    def _test(self):
        node = self.query_pick.get_node()
        if not node:
            self.log.write("Pick a query PACS first.")
            return
        my_ae = self.app.settings.my_aetitle
        self.log.write(f"C-ECHO to {node.name} ...")

        def work():
            return scu.c_echo(my_ae, node)

        def done(res):
            self.log.write("C-ECHO OK." if res.success
                           else f"C-ECHO failed: {res.message}")

        run_threaded(self, work, done,
                     lambda e: self.log.write(f"[ERROR] {e}"))

    def _lookup(self):
        if self._busy:
            return
        node = self.query_pick.get_node()
        if not node:
            self.log.write("Pick a query PACS first.")
            return
        acc = self.acc_entry.get().strip()
        pid = self.pid_entry.get().strip()
        if not acc and not pid:
            self.log.write("Enter an accession number or a patient ID.")
            return
        my_ae = self.app.settings.my_aetitle
        self.lookup_btn.configure(state="disabled")
        self.log.write(f"Querying {node.name} ...")

        def work():
            return reconcile.lookup_studies(my_ae, node, accession=acc,
                                            patient_id=pid,
                                            progress=self.log.write)

        def done(studies):
            self.lookup_btn.configure(state="normal")
            self.studies = studies
            self.chosen_study = None
            if not studies:
                self.study_menu.configure(values=["(no studies found)"])
                self.study_menu.set("(no studies found)")
                self.study_lbl.configure(text="No matching study.",
                                         text_color=_ERR)
                return
            labels = [self._study_label(s) for s in studies]
            self.study_menu.configure(values=labels)
            self.study_menu.set(labels[0])
            self._pick_study(labels[0])

        def err(exc):
            self.lookup_btn.configure(state="normal")
            self.log.write(f"[ERROR] {exc}")

        run_threaded(self, work, done, err)

    @staticmethod
    def _study_label(s: StudyContext) -> str:
        return (f"{s.accession or '(no acc)'}  -  {s.patient_name}  -  "
                f"{s.study_date}  -  {s.study_description or s.modalities}")[:90]

    def _pick_study(self, label: str):
        for s in self.studies:
            if self._study_label(s) == label:
                self.chosen_study = s
                self.study_lbl.configure(
                    text=f"Selected: {s.patient_name} (ID {s.patient_id})  "
                         f"acc {s.accession}  study {s.study_date}  "
                         f"[{s.modalities}]", text_color=MUTED)
                return

    # -- send ------------------------------------------------------------
    def _series_info(self) -> SeriesInfo:
        sel = self.modality.get().strip()
        modality = "OT" if sel == AUTO_MODALITY else (sel or "OT")
        return SeriesInfo(
            modality=modality,
            series_description=self.desc.get().strip(),
            body_part=self.body_part.get().strip().upper(),
            series_number=SERIES_NUMBER,
            conversion_type=self.conv.get().split(" - ")[0].strip() or "WSD",
        )

    def _send(self):
        if self._busy:
            return
        node = self.send_pick.get_node()
        if not node:
            self.wiz.set_message("Pick a Send PACS.", error=True)
            return
        if not self.chosen_study or not (self.pages or self.videos
                                         or self.dicoms):
            self.wiz.set_message("Need a selected study and items.", error=True)
            return
        si = self._series_info()
        if not si.series_description:
            self.wiz.set_message("Enter a series description.", error=True)
            return

        self._busy = True
        self.wiz.set_busy(True)
        self.pbar.set(0)
        self.log.write("Converting items...")

        study = self.chosen_study
        pages, videos, dicoms = list(self.pages), list(self.videos), list(
            self.dicoms)
        my_ae = self.app.settings.my_aetitle
        keep = bool(self.keep_copy.get())
        strip_private = bool(self.strip_private.get())
        auto = self.modality.get() == AUTO_MODALITY
        forced = si.modality
        job_dir = Path(tempfile.mkdtemp(prefix="p2p_job_"))

        def frac(v):
            self.after(0, lambda: self.pbar.set(v))

        def work():
            datasets = []
            snum = si.series_number
            if pages:
                if auto:
                    for mod, grp in sc_import.group_pages_by_modality(
                            pages).items():
                        datasets.extend(sc_import.convert_pages(
                            grp, study, replace(si, modality=mod,
                                                series_number=snum)))
                        snum += 1
                else:
                    datasets.extend(sc_import.convert_pages(
                        pages, study, replace(si, series_number=snum)))
                    snum += 1
            for vpath, _thumb in videos:
                self.log.write(f"Transcoding {vpath.name} ...")
                datasets.append(video.convert_video(
                    vpath, study, replace(si, modality="XC" if auto else forced,
                                          series_number=snum)))
                snum += 1
            if dicoms:
                self.log.write(f"Re-filing {len(dicoms)} DICOM object(s) ...")
                datasets.extend(dicom_import.convert_dicoms(
                    [s for s, _ in dicoms], study, replace(si, series_number=snum),
                    strip_private=strip_private))
                snum += 1
            self.log.write(f"Sending {len(datasets)} object(s) to {node.name} ...")
            return send.send_series(my_ae, node, datasets, job_dir,
                                    progress=self.log.write, on_frac=frac,
                                    cleanup=not keep)

        def done(result):
            self._busy = False
            self.wiz.set_busy(False)
            if result.failed == 0 and result.sent > 0:
                self.pbar.set(1)
                self.log.write(f"[DONE] Sent {result.sent} object(s) as a new "
                               f"series on accession {study.accession}.")
                if keep:
                    self.log.write(f"A copy is in: {job_dir}")
                self._clear()
                self.wiz.show(0)
            else:
                self.wiz.set_message(f"Sent {result.sent}, failed "
                                     f"{result.failed} - see log.", error=True)
                self.log.write(f"[WARN] Sent {result.sent}, failed "
                               f"{result.failed}.")
                for e in result.errors[:8]:
                    self.log.write(f"  {e}")

        def err(exc):
            self._busy = False
            self.wiz.set_busy(False)
            self.log.write(f"[ERROR] Send failed: {exc}")

        run_threaded(self, work, done, err)
