"""Modality SCU: Modality Worklist query + Performed Procedure Step, plus a
full "perform exam" flow (MPPS In Progress -> stamp & C-STORE images -> MPPS
Completed). Emulates an imaging modality talking to a RIS/PACS.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from pydicom import dcmread

from .. import config, paths
from ..model import DEST_GROUP_WORKLIST
from ..net import mwl, mpps, scu
from ..tools.fileops import find_dicom_files
from ..tools import modality as modtool
from .base import ToolPanel
from .batch import BatchRunner
from .theme import MUTED, mono, tool_color
from .widgets import DestinationPicker, build_drop_zone, run_threaded, PAD


class ModalitySCUPanel(ToolPanel):
    title = "Modality SCU"
    description = ("Query the Modality Worklist, then perform an exam: MPPS In "
                  "Progress, stamp + send images (C-STORE), MPPS Completed.")

    # -- build -----------------------------------------------------------
    def build(self) -> None:
        self.files: list[Path] = []
        self._items: list[dict] = []

        # Give the tabbed body most of the height; keep the log a short strip.
        self.grid_rowconfigure(1, weight=5)
        self.grid_rowconfigure(2, weight=1)
        self.log.configure(height=64)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        # Three-step workflow, one tab per step, so each gets the full height.
        # Color the active tab with the tool accent so the steps stand out.
        accent = tool_color(type(self).__name__)
        self.tabs = ctk.CTkTabview(
            self.body,
            segmented_button_fg_color=("gray72", "gray28"),
            segmented_button_selected_color=accent,
            segmented_button_selected_hover_color=accent,
            segmented_button_unselected_color=("gray72", "gray28"),
            segmented_button_unselected_hover_color=("gray66", "gray34"),
            text_color=("gray10", "gray92"))
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(0, PAD))
        try:
            self.tabs._segmented_button.configure(
                font=ctk.CTkFont(size=14, weight="bold"), height=32)
        except Exception:  # noqa: BLE001 - private attr, non-fatal styling
            pass
        t1 = self.tabs.add("1 · Worklist")
        t2 = self.tabs.add("2 · Exam images")
        t3 = self.tabs.add("3 · Perform")

        # ===== Step 1: query worklist + pick a scheduled step =====
        t1.grid_columnconfigure(0, weight=1)
        t1.grid_rowconfigure(2, weight=1)     # results expand
        self.ris = DestinationPicker(t1, self.app, label="Worklist / MPPS server",
                                     remember_key="ModalityRIS",
                                     groups=[DEST_GROUP_WORKLIST])
        self.ris.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))

        q = ctk.CTkFrame(t1)
        q.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        for c in range(6):
            q.grid_columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)
        today = datetime.now().strftime("%Y%m%d")
        self.qf: dict[str, ctk.CTkEntry] = {}
        specs = [("date", "Sched. date", today),
                 ("station", "Station AE", self.app.settings.my_aetitle),
                 ("modality", "Modality", ""),
                 ("pid", "Patient ID", ""),
                 ("pname", "Patient name", ""),
                 ("acc", "Accession", "")]
        for i, (key, label, default) in enumerate(specs):
            r, c = divmod(i, 3)
            ctk.CTkLabel(q, text=label, anchor="w", width=90).grid(
                row=r, column=c * 2, sticky="w", padx=(PAD, 2), pady=3)
            e = ctk.CTkEntry(q, width=150)
            e.insert(0, default)
            e.grid(row=r, column=c * 2 + 1, sticky="ew", padx=(0, PAD), pady=3)
            self.qf[key] = e
        qbar = ctk.CTkFrame(q, fg_color="transparent")
        qbar.grid(row=2, column=0, columnspan=6, sticky="w", padx=PAD, pady=(2, PAD))
        self.query_btn = ctk.CTkButton(qbar, text="Query Worklist",
                                       command=self._query)
        self.query_btn.pack(side="left")
        ctk.CTkButton(qbar, text="Today + this station", width=150,
                      fg_color="transparent", border_width=1,
                      command=self._reset_query).pack(side="left", padx=PAD)
        self.query_status = ctk.CTkLabel(qbar, text="", text_color=MUTED)
        self.query_status.pack(side="left", padx=PAD)

        self.results = ctk.CTkScrollableFrame(
            t1, label_text="Scheduled procedure steps (pick one, then open "
                            "'2 · Exam images')")
        self.results.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(0, PAD))
        self.results.grid_columnconfigure(0, weight=1)
        self._sel = ctk.IntVar(value=-1)

        # ===== Step 2: load the images acquired for this exam =====
        t2.grid_columnconfigure(0, weight=1)
        t2.grid_rowconfigure(2, weight=1)
        self.pacs = DestinationPicker(t2, self.app, label="Send images to (PACS)",
                                      remember_key="ModalityPACS")
        self.pacs.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        ctk.CTkLabel(t2, text="Load the images acquired for the selected exam:",
                     anchor="w", text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=PAD, pady=(PAD, 2))
        zone, self.count_lbl = build_drop_zone(
            self.app, t2, self._on_drop, "go to '3 · Perform'",
            [("Load Folder...", self._load_folder, 130),
             ("Load Files...", self._load_files, 120),
             ("Clear", self._clear_files, 70)])
        zone.grid(row=2, column=0, sticky="new", padx=PAD, pady=(0, PAD))

        # ===== Step 3: perform (MPPS -> store -> MPPS) =====
        t3.grid_columnconfigure(0, weight=1)
        t3.grid_rowconfigure(4, weight=1)     # runner expands
        self.sel_banner = ctk.CTkLabel(
            t3, text="No worklist item selected - pick one on step 1.",
            anchor="w", justify="left", text_color=MUTED,
            font=ctk.CTkFont(size=14, weight="bold"))
        self.sel_banner.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 2))
        self.exam_summary = ctk.CTkLabel(t3, text="", anchor="w",
                                         text_color=MUTED)
        self.exam_summary.grid(row=1, column=0, sticky="w", padx=PAD, pady=(0, PAD))

        opt = ctk.CTkFrame(t3, fg_color="transparent")
        opt.grid(row=2, column=0, sticky="ew", padx=PAD)
        self.opt_mpps = ctk.CTkCheckBox(opt, text="Send MPPS (In Progress / Completed)")
        self.opt_mpps.select()
        self.opt_mpps.pack(side="left")
        self.opt_store = ctk.CTkCheckBox(opt, text="Send images (C-STORE)")
        self.opt_store.select()
        self.opt_store.pack(side="left", padx=PAD)
        ctk.CTkLabel(opt, text="Finish as:").pack(side="left", padx=(PAD, 4))
        self.mpps_status = ctk.CTkOptionMenu(opt, values=["COMPLETED", "DISCONTINUED"],
                                             width=140)
        self.mpps_status.pack(side="left")

        runbar = ctk.CTkFrame(t3, fg_color="transparent")
        runbar.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        self.perform_btn = ctk.CTkButton(runbar, text="Perform exam",
                                         command=self._perform)
        self.perform_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(runbar, text="Cancel", width=80,
                                        command=self._cancel, state="disabled",
                                        fg_color="#a33", hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)
        ctk.CTkLabel(runbar, text="Parallel").pack(side="left", padx=(PAD, 4))
        self.workers = ctk.CTkOptionMenu(runbar, values=["1", "2", "4", "6", "8"],
                                         width=64)
        self.workers.set(str(self.app.settings.send_workers))
        self.workers.pack(side="left")

        self.runner = BatchRunner(self, verb="sent")
        self.runner.build(t3).grid(row=4, column=0, sticky="nsew",
                                   padx=PAD, pady=(PAD, PAD))
        self._refresh_exam_summary()

    def on_destinations_changed(self) -> None:
        self.ris.refresh()
        self.pacs.refresh()
        self._refresh_exam_summary()

    # -- worklist --------------------------------------------------------
    def _reset_query(self) -> None:
        self.qf["date"].delete(0, "end")
        self.qf["date"].insert(0, datetime.now().strftime("%Y%m%d"))
        self.qf["station"].delete(0, "end")
        self.qf["station"].insert(0, self.app.settings.my_aetitle)
        for k in ("modality", "pid", "pname", "acc"):
            self.qf[k].delete(0, "end")

    def _query(self) -> None:
        node = self.ris.get_node()
        if not node:
            self.log.write("Select a Worklist / MPPS server.")
            return
        self.query_btn.configure(state="disabled")
        self.query_status.configure(text="Querying...")
        ident = mwl.build_worklist_query(
            sps_start_date=self.qf["date"].get().strip(),
            sps_station_ae=self.qf["station"].get().strip(),
            modality=self.qf["modality"].get().strip(),
            patient_id=self.qf["pid"].get().strip(),
            patient_name=self.qf["pname"].get().strip(),
            accession=self.qf["acc"].get().strip())
        my_ae = self.app.settings.my_aetitle
        tls = self.app.tls_args_for(node)
        self.log.write(f"--- Worklist C-FIND to {node.name} ---")

        def work():
            return mwl.query_worklist(my_ae, node, ident, timeout=node.timeout,
                                      progress=self.progress, tls_args=tls)

        def done(result):
            self._items = [mwl.flatten_item(d) for d in result.datasets]
            self._render_results()
            self.query_status.configure(
                text=f"{len(self._items)} item(s).",
                text_color=MUTED)
            self.log.write(f"[DONE] {result.message}")
            self.query_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.query_status.configure(text="query failed", text_color="#d9695f")
            self.query_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _render_results(self) -> None:
        for w in self.results.winfo_children():
            w.destroy()
        self._sel.set(-1)
        self.sel_banner.configure(
            text="No worklist item selected - pick one on step 1.",
            text_color=MUTED)
        if not self._items:
            ctk.CTkLabel(self.results, text="No worklist items.",
                         text_color=MUTED).grid(sticky="w", padx=4, pady=4)
            return
        header = (f"{'Patient':22} {'ID':12} {'Mod':4} {'Station':12} "
                  f"{'Date':8} {'Time':6} {'Accession':12} Procedure")
        ctk.CTkLabel(self.results, text=header, anchor="w", font=mono(11),
                     text_color=MUTED).grid(sticky="w", padx=4)
        for i, it in enumerate(self._items):
            t = it.get("ScheduledProcedureStepStartTime", "")[:6]
            line = (f"{it['PatientName'][:22]:22} {it['PatientID'][:12]:12} "
                    f"{it['Modality'][:4]:4} {it['ScheduledStationAETitle'][:12]:12} "
                    f"{it['ScheduledProcedureStepStartDate']:8} {t:6} "
                    f"{it['AccessionNumber'][:12]:12} "
                    f"{it['RequestedProcedureDescription']}")
            ctk.CTkRadioButton(self.results, text=line, font=mono(11),
                               variable=self._sel, value=i,
                               command=self._on_pick).grid(
                sticky="w", padx=4, pady=1)

    def _selected_item(self) -> dict | None:
        i = self._sel.get()
        if 0 <= i < len(self._items):
            return self._items[i]
        return None

    def _on_pick(self) -> None:
        it = self._selected_item()
        if it:
            self.sel_banner.configure(
                text=f"Selected:  {it.get('PatientName','?')}   ·   "
                     f"{it.get('PatientID','')}   ·   Acc {it.get('AccessionNumber','')}"
                     f"   ·   {it.get('Modality','')}   ·   "
                     f"{it.get('RequestedProcedureDescription','')}",
                text_color=("#2e8b57", "#43c59e"))

    # -- exam images -----------------------------------------------------
    def _load_files(self) -> None:
        from tkinter import filedialog
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if chosen:
            self.files = [Path(p) for p in chosen]
            self._update_count()

    def _load_folder(self) -> None:
        from tkinter import filedialog
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.count_lbl.configure(text="Scanning folder...")

        def work():
            return find_dicom_files(Path(folder))

        def done(found):
            self.files = list(found)
            self.log.write(f"Loaded {len(found):,} exam image(s) from {folder}")
            self._update_count()

        run_threaded(self, work, done)

    def _on_drop(self, dropped: list) -> None:
        chosen = [Path(p) for p in dropped]
        loose = [p for p in chosen if p.is_file()]
        folders = [p for p in chosen if p.is_dir()]
        if folders:
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                out = list(loose)
                for d in folders:
                    out.extend(find_dicom_files(d))
                return out

            def done(found):
                self.files = list(found)
                self.log.write(f"Loaded {len(found):,} exam image(s).")
                self._update_count()

            run_threaded(self, work, done)
        else:
            self.files = loose
            self._update_count()

    def _clear_files(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} exam image(s).")
        self._refresh_exam_summary()

    def _refresh_exam_summary(self) -> None:
        pacs = self.pacs.get_node()
        self.exam_summary.configure(
            text=f"Images loaded: {len(self.files):,}     ·     "
                 f"PACS: {pacs.name if pacs else '(none selected)'}")

    def requeue(self, requeue_paths: list) -> None:
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

    def _cancel(self) -> None:
        self.runner.cancel()

    # -- perform exam ----------------------------------------------------
    def _perform(self) -> None:
        item = self._selected_item()
        if not item:
            self.log.write("Pick a worklist item first.")
            return
        ris = self.ris.get_node()
        if not ris:
            self.log.write("Select a Worklist / MPPS server.")
            return
        do_mpps = bool(self.opt_mpps.get())
        do_store = bool(self.opt_store.get())
        if not do_mpps and not do_store:
            self.log.write("Nothing to do - enable MPPS and/or Send images.")
            return
        pacs = self.pacs.get_node() if do_store else None
        if do_store and not pacs:
            self.log.write("Select a PACS to send images to (or uncheck Send).")
            return
        if do_store and not self.files:
            self.log.write("Load the exam images to send (or uncheck Send).")
            return

        my_ae = self.app.settings.my_aetitle
        status = self.mpps_status.get()
        files = list(self.files)
        try:
            workers = int(self.workers.get())
        except ValueError:
            workers = 4
        if self.app.settings.send_workers != workers:
            self.app.settings.send_workers = workers
            config.save_settings(self.app.settings)

        # Modality: scheduled value, else the first image's, else OT.
        modality = item.get("Modality") or ""
        if not modality and files:
            try:
                modality = str(dcmread(str(files[0]), force=True,
                                       stop_before_pixels=True).get("Modality", "OT"))
            except Exception:  # noqa: BLE001
                modality = "OT"
        modality = modality or "OT"

        self.perform_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(f"=== Perform exam: {item.get('PatientName','?')} / "
                       f"{item.get('AccessionNumber','?')} "
                       f"(MPPS={'on' if do_mpps else 'off'}, "
                       f"store={'on' if do_store else 'off'}) ===")

        stamp_dir = paths.data_dir() / "exams" / (
            (item.get("AccessionNumber") or "exam") + "_" +
            datetime.now().strftime("%Y%m%d_%H%M%S"))

        state = {"mpps_uid": None, "series": [], "store_files": [], "err": None}

        # Phase A: MPPS In Progress (N-CREATE) + stamp images.
        def phase_a():
            if do_mpps:
                ds, sop_uid = mpps.build_create(
                    item, performed_station_ae=my_ae, modality=modality)
                ok, detail = mpps.mpps_create(my_ae, ris, ds, sop_uid,
                                              timeout=ris.timeout,
                                              progress=self.progress)
                if not ok:
                    state["err"] = f"MPPS N-CREATE failed: {detail}"
                    return
                state["mpps_uid"] = sop_uid
                self.log.write(f"[MPPS] IN PROGRESS created ({detail})")
            if do_store:
                self.progress(f"Stamping {len(files):,} image(s)...")
                res = modtool.stamp_exam(
                    files, item, stamp_dir,
                    store_ae=(pacs.aetitle if pacs else ""), workers=workers)
                state["series"] = res.series
                state["store_files"] = res.written
                self.log.write(f"[STAMP] {len(res.written):,} image(s) in "
                               f"{len(res.series)} series"
                               f"{' (' + str(res.failed) + ' failed)' if res.failed else ''}")
            return

        def after_a(_r):
            if state["err"]:
                self.log.write(f"[ABORT] {state['err']}")
                self._finish_exam()
                return
            if do_store and state["store_files"]:
                self._store_then_finish(my_ae, pacs, state, do_mpps, ris,
                                        status, workers)
            else:
                self._mpps_complete(my_ae, ris, state, do_mpps, status)

        def err_a(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self._finish_exam()

        run_threaded(self, phase_a, after_a, err_a)

    def _store_then_finish(self, my_ae, pacs, state, do_mpps, ris, status,
                           workers) -> None:
        files = state["store_files"]
        total = len(files)
        tls = self.app.tls_args_for(pacs)
        folder_totals = {str(files[0].parent): total} if files else {}
        self.log.write(f"--- C-STORE {total:,} image(s) to {pacs.name} ---")

        def worker():
            return scu.c_store(my_ae, pacs, files, progress=self.progress,
                               should_cancel=lambda: self.runner.cancelled,
                               timeout=pacs.timeout, tls_args=tls,
                               on_file=self.runner.on_item, workers=workers)

        def on_done():
            self._mpps_complete(my_ae, ris, state, do_mpps, status)

        self.runner.run(total, "modality", worker, on_done=on_done,
                        folder_totals=folder_totals)

    def _mpps_complete(self, my_ae, ris, state, do_mpps, status) -> None:
        if not do_mpps or not state["mpps_uid"]:
            self._finish_exam()
            return
        # If the exam was cancelled mid-store, discontinue instead of complete.
        final = "DISCONTINUED" if self.runner.cancelled else status
        set_ds = mpps.build_set(final, state["series"])
        uid = state["mpps_uid"]

        def work():
            return mpps.mpps_set(my_ae, ris, set_ds, uid, timeout=ris.timeout,
                                 progress=self.progress)

        def done(res):
            ok, detail = res
            self.log.write(f"[MPPS] {detail}" if ok
                           else f"[MPPS] FAILED: {detail}")
            self._finish_exam(final)

        def err(exc, tb):
            self.log.write(f"[ERROR] MPPS N-SET: {exc}")
            self._finish_exam()

        run_threaded(self, work, done, err)

    def _finish_exam(self, final: str | None = None) -> None:
        self.perform_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if final:
            self.log.write(f"=== Exam finished ({final}) ===")
        else:
            self.log.write("=== Exam finished ===")
