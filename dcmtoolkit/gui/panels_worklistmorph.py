"""Worklist Morph: build a test study from a real one + a worklist order.

Attended flow (one order per run):
  1. Query a Modality Worklist, pick a scheduled order.
  2. Search the Production VNA (C-FIND) for a study matching the order's
     description, pick one.
  3. Pick a Test VNA, choose Scrub (default) or Verbatim, and run:
     C-GET the source study -> morph it to the order's identity -> C-STORE to the
     Test VNA -> delete the local copy.

Scrub mode runs the de-identification engine (Basic Profile + structural sanitize
+ burned-in guard) before stamping the order identity, so no real PHI reaches the
Test VNA. Verbatim mode only re-stamps identity and mints fresh UIDs - use it only
when the Test VNA is an approved PHI environment. The engine lives in
``tools/worklistmorph``; this panel is the wiring.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from .. import paths
from ..model import DEST_GROUP_STORAGE, DEST_GROUP_WORKLIST
from ..net import mwl, scu
from ..tools import worklistmorph as wm
from .base import ToolPanel
from .theme import MUTED, mono, tool_color
from .widgets import DestinationPicker, run_threaded, PAD


class WorklistMorphPanel(ToolPanel):
    title = "Worklist Morph"
    description = ("Re-stamp a real study with a worklist order's identity and send "
                  "it to a test VNA (scrubbed of real PHI by default).")

    # -- build -----------------------------------------------------------
    def build(self) -> None:
        self._orders: list = []        # raw MWL item datasets
        self._order_items: list = []   # flattened dicts for display
        self._matches: list = []       # raw C-FIND study datasets

        self.grid_rowconfigure(1, weight=5)
        self.grid_rowconfigure(2, weight=1)
        self.log.configure(height=72)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        accent = tool_color(type(self).__name__)
        self.tabs = ctk.CTkTabview(
            self.body, segmented_button_selected_color=accent,
            segmented_button_selected_hover_color=accent)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(0, PAD))
        t1 = self.tabs.add("1 · Order")
        t2 = self.tabs.add("2 · Source study")
        t3 = self.tabs.add("3 · Send")

        self._build_order_tab(t1)
        self._build_source_tab(t2)
        self._build_send_tab(t3)

    # ===== Step 1: worklist order =====
    def _build_order_tab(self, t) -> None:
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(2, weight=1)
        self.ris = DestinationPicker(t, self.app, label="Worklist / MPPS server",
                                     remember_key="WorklistMorphRIS",
                                     groups=[DEST_GROUP_WORKLIST])
        self.ris.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))

        qf = ctk.CTkFrame(t, fg_color="transparent")
        qf.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.qf: dict = {}
        for i, (key, label) in enumerate([
                ("date", "SPS date (YYYYMMDD)"), ("modality", "Modality"),
                ("station", "Station AE"), ("acc", "Accession"),
                ("pid", "Patient ID"), ("pname", "Patient name")]):
            ctk.CTkLabel(qf, text=label).grid(row=i // 3, column=(i % 3) * 2,
                                              sticky="e", padx=(0, 4), pady=2)
            e = ctk.CTkEntry(qf, width=150)
            e.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(0, 10))
            self.qf[key] = e

        bar = ctk.CTkFrame(t, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=PAD)
        self.query_btn = ctk.CTkButton(bar, text="Query worklist",
                                       command=self._query)
        self.query_btn.pack(side="left")
        self.query_status = ctk.CTkLabel(bar, text="", text_color=MUTED)
        self.query_status.pack(side="left", padx=10)

        self.orders = ctk.CTkScrollableFrame(t, height=220)
        self.orders.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self._osel = ctk.IntVar(value=-1)

    def _query(self) -> None:
        node = self.ris.get_node()
        if not node:
            self.log.write("Select a Worklist / MPPS server.")
            return
        ident = mwl.build_worklist_query(
            sps_start_date=self.qf["date"].get().strip(),
            sps_station_ae=self.qf["station"].get().strip(),
            modality=self.qf["modality"].get().strip(),
            patient_id=self.qf["pid"].get().strip(),
            patient_name=self.qf["pname"].get().strip(),
            accession=self.qf["acc"].get().strip())
        my_ae = self.app.settings.my_aetitle
        tls = self.app.tls_args_for(node)
        self.query_btn.configure(state="disabled")
        self.query_status.configure(text="Querying...")
        self.log.write(f"--- Worklist C-FIND to {node.name} ---")

        def work():
            return mwl.query_worklist(my_ae, node, ident, timeout=node.timeout,
                                      progress=self.progress, tls_args=tls)

        def done(result):
            self._orders = list(result.datasets)
            self._order_items = [mwl.flatten_item(d) for d in result.datasets]
            self._render_orders()
            self.query_status.configure(text=f"{len(self._orders)} order(s).",
                                        text_color=MUTED)
            self.log.write(f"[DONE] {result.message}")
            self.query_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.query_status.configure(text="query failed", text_color="#d9695f")
            self.query_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _render_orders(self) -> None:
        for w in self.orders.winfo_children():
            w.destroy()
        self._osel.set(-1)
        if not self._order_items:
            ctk.CTkLabel(self.orders, text="No worklist orders.",
                         text_color=MUTED).grid(sticky="w", padx=4, pady=4)
            return
        for i, it in enumerate(self._order_items):
            line = (f"{it.get('PatientName', '')[:22]:22} "
                    f"{it.get('AccessionNumber', '')[:12]:12} "
                    f"{it.get('Modality', '')[:4]:4} "
                    f"{it.get('RequestedProcedureDescription', '')}")
            ctk.CTkRadioButton(self.orders, text=line, font=mono(11),
                               variable=self._osel, value=i,
                               command=self._on_order_pick).grid(
                sticky="w", padx=4, pady=1)

    def _on_order_pick(self) -> None:
        ctx = self._selected_order_ctx()
        if ctx is None:
            return
        # Prefill the source-study search with the order's description.
        self.find_desc.delete(0, "end")
        self.find_desc.insert(0, ctx.study_description)
        self.order_banner.configure(
            text=f"Order: {ctx.patient_name}  ·  Acc {ctx.accession}  ·  "
            f"{ctx.study_description}", text_color=MUTED)

    def _selected_order_ctx(self):
        i = self._osel.get()
        if 0 <= i < len(self._orders):
            return wm.order_to_context(self._orders[i])
        return None

    # ===== Step 2: source study on the Production VNA =====
    def _build_source_tab(self, t) -> None:
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(3, weight=1)
        self.order_banner = ctk.CTkLabel(
            t, text="Pick an order on step 1 first.", text_color=MUTED)
        self.order_banner.grid(row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 0))
        self.prod = DestinationPicker(t, self.app, label="Production VNA (source)",
                                      remember_key="WorklistMorphProd",
                                      groups=[DEST_GROUP_STORAGE])
        self.prod.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(4, 0))

        sf = ctk.CTkFrame(t, fg_color="transparent")
        sf.grid(row=2, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkLabel(sf, text="Study description").pack(side="left")
        self.find_desc = ctk.CTkEntry(sf, width=240)
        self.find_desc.pack(side="left", padx=6)
        ctk.CTkLabel(sf, text="Modality").pack(side="left")
        self.find_mod = ctk.CTkEntry(sf, width=70)
        self.find_mod.pack(side="left", padx=6)
        self.find_btn = ctk.CTkButton(sf, text="Find studies", command=self._find)
        self.find_btn.pack(side="left", padx=6)
        self.find_status = ctk.CTkLabel(sf, text="", text_color=MUTED)
        self.find_status.pack(side="left", padx=6)

        self.matches = ctk.CTkScrollableFrame(t, height=220)
        self.matches.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self._msel = ctk.IntVar(value=-1)

    def _find(self) -> None:
        node = self.prod.get_node()
        if not node:
            self.log.write("Select the Production VNA.")
            return
        desc = self.find_desc.get().strip()
        if not desc:
            self.log.write("Enter a study description to match (from the order).")
            return
        ident = scu.build_query("STUDY", StudyDescription=desc,
                                Modality=self.find_mod.get().strip(),
                                StudyInstanceUID="", PatientID="",
                                PatientName="", StudyDate="", AccessionNumber="")
        my_ae = self.app.settings.my_aetitle
        tls = self.app.tls_args_for(node)
        self.find_btn.configure(state="disabled")
        self.find_status.configure(text="Searching...")
        self.log.write(f"--- C-FIND to {node.name} (desc={desc!r}) ---")

        def work():
            return scu.c_find(my_ae, node, ident, model="STUDY",
                              progress=self.progress, tls_args=tls)

        def done(result):
            self._matches = list(result.datasets)
            self._render_matches()
            self.find_status.configure(text=f"{len(self._matches)} match(es).",
                                       text_color=MUTED)
            self.log.write(f"[DONE] {result.message}")
            self.find_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.find_status.configure(text="search failed", text_color="#d9695f")
            self.find_btn.configure(state="normal")

        run_threaded(self, work, done, err)

    def _render_matches(self) -> None:
        for w in self.matches.winfo_children():
            w.destroy()
        self._msel.set(-1)
        if not self._matches:
            ctk.CTkLabel(self.matches, text="No matching studies.",
                         text_color=MUTED).grid(sticky="w", padx=4, pady=4)
            return
        ctk.CTkLabel(self.matches,
                     text="Pick ONE source study (its pixels will be reused):",
                     text_color=MUTED).grid(sticky="w", padx=4)
        for i, ds in enumerate(self._matches):
            def g(kw):
                v = getattr(ds, kw, "")
                return "" if v is None else str(v)
            line = (f"{g('StudyDate')[:8]:8}  {g('Modality')[:4]:4}  "
                    f"{g('StudyDescription')[:30]:30}  "
                    f"imgs:{g('NumberOfStudyRelatedInstances') or '?'}")
            ctk.CTkRadioButton(self.matches, text=line, font=mono(11),
                               variable=self._msel, value=i).grid(
                sticky="w", padx=4, pady=1)

    # ===== Step 3: morph + send =====
    def _build_send_tab(self, t) -> None:
        t.grid_columnconfigure(0, weight=1)
        self.test = DestinationPicker(t, self.app, label="Test VNA (destination)",
                                      remember_key="WorklistMorphTest",
                                      groups=[DEST_GROUP_STORAGE])
        self.test.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))

        opts = ctk.CTkFrame(t, fg_color="transparent")
        opts.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.verbatim = ctk.CTkCheckBox(
            opts, text="Verbatim (no scrub — Test VNA must be PHI-approved)",
            command=self._on_mode_toggle)
        self.verbatim.grid(row=0, column=0, sticky="w")
        self.allow_burned = ctk.CTkCheckBox(
            opts, text="Allow burned-in (scrub mode; skip the guard)")
        self.allow_burned.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.mode_warn = ctk.CTkLabel(opts, text="", text_color="#d9695f")
        self.mode_warn.grid(row=2, column=0, sticky="w", pady=(4, 0))

        self.run_btn = ctk.CTkButton(t, text="Morph & send", command=self._run)
        self.run_btn.grid(row=2, column=0, sticky="w", padx=PAD, pady=(0, PAD))
        self.run_status = ctk.CTkLabel(t, text="", text_color=MUTED)
        self.run_status.grid(row=3, column=0, sticky="w", padx=PAD)

    def _on_mode_toggle(self) -> None:
        if self.verbatim.get():
            self.mode_warn.configure(
                text="Verbatim: the real study's other PHI (and burned-in pixels) "
                "will be sent unchanged. Only use a PHI-approved Test VNA.")
        else:
            self.mode_warn.configure(text="")

    def _run(self) -> None:
        ctx = self._selected_order_ctx()
        if ctx is None:
            self.log.write("Pick a worklist order on step 1.")
            return
        mi = self._msel.get()
        if not (0 <= mi < len(self._matches)):
            self.log.write("Pick a source study on step 2.")
            return
        prod = self.prod.get_node()
        test = self.test.get_node()
        if not prod or not test:
            self.log.write("Select both the Production VNA and the Test VNA.")
            return
        src_study_uid = str(getattr(self._matches[mi], "StudyInstanceUID", ""))
        if not src_study_uid:
            self.log.write("Selected study has no Study Instance UID.")
            return

        my_ae = self.app.settings.my_aetitle
        tls_prod = self.app.tls_args_for(prod)
        tls_test = self.app.tls_args_for(test)
        verbatim = bool(self.verbatim.get())
        allow = bool(self.allow_burned.get())
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        temp = paths.data_dir() / "worklist-morph" / stamp

        def cleanup():
            shutil.rmtree(temp, ignore_errors=True)

        self.run_btn.configure(state="disabled")
        self.run_status.configure(text="Running...")
        self.log.write(f"--- Worklist morph: {prod.name} -> {test.name} "
                       f"({'verbatim' if verbatim else 'scrub'}) ---")

        def work():
            src = temp / "src"
            out = temp / "out"
            src.mkdir(parents=True, exist_ok=True)
            ident = scu.build_query("STUDY", StudyInstanceUID=src_study_uid)
            scu.c_get(my_ae, prod, ident, src, model="STUDY",
                      progress=self.progress, tls_args=tls_prod)
            files = [p for p in src.rglob("*") if p.is_file()]
            if not files:
                raise RuntimeError("C-GET returned no objects.")
            res = wm.morph_files(files, ctx, out, verbatim=verbatim,
                                 allow_burned_in=allow, progress=self.progress)
            if not res.written:
                raise RuntimeError(
                    "No objects to send after morph (all failed; see log).")
            scu.c_store(my_ae, test, res.written, progress=self.progress,
                        tls_args=tls_test)
            return res

        def done(res):
            cleanup()
            msg = (f"Sent {len(res.written)} object(s) to {test.name}"
                   + (f"; {len(res.failed)} failed" if res.failed else ""))
            self.run_status.configure(text=msg, text_color=MUTED)
            for f in res.failed:
                self.log.write(f"  failed: {f}")
            self.log.write(f"[DONE] {msg}")
            self.run_btn.configure(state="normal")

        def err(exc, tb):
            cleanup()
            self.log.write(f"[ERROR] {exc}")
            self.run_status.configure(text="failed", text_color="#d9695f")
            self.run_btn.configure(state="normal")

        run_threaded(self, work, done, err)
