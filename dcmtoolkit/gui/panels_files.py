"""File tool panels: Tag List, Modify, Split Multiframe, Dump."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..tools import fileops
from ..store.processing import ReceiverConfig
from .base import ToolPanel
from .batch import BatchRunner
from .theme import MUTED
from .widgets import build_drop_zone, run_threaded, PAD


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split("|") if p.strip()]


def _folder_totals(files) -> dict:
    totals: dict = {}
    for f in files:
        k = str(Path(f).parent)
        totals[k] = totals.get(k, 0) + 1
    return totals


class TagListPanel(ToolPanel):
    title = "Tag Lister"
    description = "Inspect the DICOM header of a single file."

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Open DICOM file...",
                      command=self._open).pack(side="left")
        self.path_lbl = ctk.CTkLabel(bar, text="", text_color=MUTED)
        self.path_lbl.pack(side="left", padx=PAD)

    def _open(self) -> None:
        p = filedialog.askopenfilename(
            title="Select a DICOM file",
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if not p:
            return
        self.path_lbl.configure(text=Path(p).name)
        self.log.clear()

        def work():
            return fileops.list_tags(Path(p))

        def done(rows):
            self.log.write(f"{'Tag':<24}{'VR':<4}{'Keyword':<28}Value")
            self.log.write("-" * 90)
            for r in rows:
                self.log.write(f"{r.tag:<24}{r.vr:<4}{r.keyword:<28}{r.value}")
            self.log.write(f"\n{len(rows)} element(s).")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")

        run_threaded(self, work, done, err)


class _DropSourceMixin:
    """Shared Add Files / Add Folder / drag-drop into ``self.files``."""

    def _make_source(self, action_verb: str = "Run", extra_buttons=None):
        specs = [("Add Files...", self._add_files, 110),
                 ("Add Folder...", self._add_folder, 120)]
        if extra_buttons:
            specs += extra_buttons
        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, action_verb, specs)
        return zone

    def _add_files(self):
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in chosen)
        self._update_count()

    def _add_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.count_lbl.configure(text="Scanning folder...")

        def work():
            return fileops.find_dicom_files(Path(folder))

        def done(found):
            self.files.extend(found)
            self.log.write(f"Added {len(found):,} file(s) from {folder}")
            self._update_count()

        run_threaded(self, work, done)

    def _on_drop(self, dropped: list):
        chosen = [Path(p) for p in dropped]
        loose = [p for p in chosen if p.is_file()]
        self.files.extend(loose)
        if loose:
            self.log.write(f"Added {len(loose):,} dropped file(s).")
        folders = [p for p in chosen if p.is_dir()]
        if folders:
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                return [(d, fileops.find_dicom_files(d)) for d in folders]

            def done(results):
                for d, found in results:
                    self.files.extend(found)
                    self.log.write(f"  + {d.name}:  {len(found):,} file(s)")
                self._update_count()

            run_threaded(self, work, done)
        else:
            self._update_count()

    def _update_count(self):
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")

    def requeue(self, requeue_paths: list):
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

    def _cancel(self):
        self.runner.cancel()


class ModifyPanel(_DropSourceMixin, ToolPanel):
    title = "Modify Header"
    description = ("Set or remove tags across files. Tags as ggggeeee "
                   "(e.g. 00100010).")

    def build(self) -> None:
        self.files: list[Path] = []
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(4, weight=1)

        self._make_source("Apply").grid(row=0, column=0, sticky="ew", padx=PAD,
                                        pady=PAD)

        opsframe = ctk.CTkFrame(self.body)
        opsframe.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkLabel(opsframe, text="Action").grid(row=0, column=0, padx=PAD)
        ctk.CTkLabel(opsframe, text="Tag (ggggeeee)").grid(row=0, column=1)
        ctk.CTkLabel(opsframe, text="New value (for Set)").grid(row=0, column=2)
        self.op_rows = []
        for i in range(4):
            action = ctk.CTkOptionMenu(opsframe, values=["set", "remove"],
                                       width=90)
            action.grid(row=i + 1, column=0, padx=PAD, pady=2)
            tag = ctk.CTkEntry(opsframe, width=120)
            tag.grid(row=i + 1, column=1, padx=PAD, pady=2)
            val = ctk.CTkEntry(opsframe, width=260)
            val.grid(row=i + 1, column=2, padx=PAD, pady=2)
            self.op_rows.append((action, tag, val))

        self.in_place = ctk.CTkCheckBox(
            self.body, text="Overwrite files in place (else write to ./modified)")
        self.in_place.grid(row=2, column=0, sticky="w", padx=PAD, pady=PAD)

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=3, column=0, sticky="w", padx=PAD, pady=(0, PAD))
        self.btn = ctk.CTkButton(btnbar, text="Apply", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btnbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.runner = BatchRunner(self, verb="changed")
        self.runner.build(self.body).grid(row=4, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))

    def _run(self) -> None:
        if not self.files:
            self.log.write("No files selected.")
            return
        ops = []
        for action, tag, val in self.op_rows:
            t = tag.get().strip()
            if t:
                ops.append(fileops.ModifyOp(tag=t, action=action.get(),
                                            value=val.get()))
        if not ops:
            self.log.write("No operations defined.")
            return
        files = list(self.files)
        total = len(files)
        in_place = bool(self.in_place.get())
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(f"--- Modifying {total:,} file(s) ---")

        def worker():
            return fileops.modify_files(
                files, ops, in_place=in_place, progress=self.progress,
                on_item=self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled)

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")

        self.runner.run(total, "modify", worker, on_done=on_done,
                        folder_totals=_folder_totals(files))


class SplitPanel(_DropSourceMixin, ToolPanel):
    title = "Split Multiframe"
    description = "Split multi-frame DICOM objects into single-frame files."

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(3, weight=1)

        self._make_source("Split").grid(row=0, column=0, sticky="ew", padx=PAD,
                                        pady=PAD)

        outbar = ctk.CTkFrame(self.body, fg_color="transparent")
        outbar.grid(row=1, column=0, sticky="ew", padx=PAD)
        ctk.CTkButton(outbar, text="Output folder...",
                      command=self._pick_out).pack(side="left")
        self.out_lbl = ctk.CTkLabel(outbar, text="(not set)",
                                    text_color=MUTED)
        self.out_lbl.pack(side="left", padx=PAD)

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=2, column=0, sticky="w", padx=PAD, pady=PAD)
        self.btn = ctk.CTkButton(btnbar, text="Split", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btnbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.runner = BatchRunner(self, verb="split")
        self.runner.build(self.body).grid(row=3, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=folder)

    def _run(self) -> None:
        if not self.files:
            self.log.write("No files selected.")
            return
        out = self.out_dir or (self.files[0].parent / "split")
        files = list(self.files)
        total = len(files)
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(f"--- Splitting {total:,} file(s) -> {out} ---")

        def worker():
            return fileops.split_multiframe(
                files, out, progress=self.progress,
                on_item=self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled)

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")

        self.runner.run(total, "split", worker, on_done=on_done,
                        folder_totals=_folder_totals(files))


class DumpPanel(_DropSourceMixin, ToolPanel):
    title = "Folder Dump"
    description = "Scan files/folders and export study/exam metadata to CSV."

    def build(self) -> None:
        self.files: list[Path] = []
        self._last_dump = None
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)

        self._make_source("Scan").grid(row=0, column=0, sticky="ew", padx=PAD,
                                       pady=PAD)

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=1, column=0, sticky="w", padx=PAD, pady=PAD)
        self.btn = ctk.CTkButton(btnbar, text="Scan + Export CSV",
                                 command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btnbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.runner = BatchRunner(self, verb="read")
        self.runner.build(self.body).grid(row=2, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))

    def _run(self) -> None:
        if not self.files:
            self.log.write("Add files or a folder first.")
            return
        files = list(self.files)
        total = len(files)
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(f"--- Scanning {total:,} file(s) ---")

        def worker():
            self._last_dump = fileops.dump_files(
                files, progress=self.progress, on_item=self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled)
            return self._last_dump

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            res = self._last_dump
            if res and res.rows:
                out = filedialog.asksaveasfilename(
                    defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                    initialfile="dump.csv")
                if out:
                    fileops.write_dump_csv(res, Path(out))
                    self.log.write(f"Saved {len(res.rows):,} rows to {out}")

        self.runner.run(total, "dump", worker, on_done=on_done,
                        folder_totals=_folder_totals(files))


class DeidentifyPanel(ToolPanel):
    title = "De-identify Files"
    description = ("Anonymize / morph / pixel-blank local DICOM files to an "
                  "output folder (same engine as the Store Receiver).")

    def build(self) -> None:
        self.files: list[Path] = []
        self.base_dir: Path | None = None
        self.out_dir: Path | None = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=80)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(3, weight=1)

        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "De-identify",
            [("Add Files...", self._add_files, 110),
             ("Add Folder...", self._add_folder, 120),
             ("Output folder...", self._pick_out, 130)])
        zone.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)

        form = ctk.CTkScrollableFrame(self.body, height=230)
        form.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        form.grid_columnconfigure(1, weight=1)
        r = 0

        def row(label, widget):
            nonlocal r
            ctk.CTkLabel(form, text=label, anchor="w", width=170).grid(
                row=r, column=0, sticky="w", padx=PAD, pady=3)
            widget.grid(row=r, column=1, sticky="ew", padx=PAD, pady=3)
            r += 1
            return widget

        def entry(width=280):
            return ctk.CTkEntry(form, width=width)

        self.rm_private = ctk.CTkCheckBox(form, text="Remove private tags")
        self.rm_private.select()
        row("", self.rm_private)
        self.rm_groups = row("Remove Groups (gggg|..)", entry())
        self.rm_tags = row("Remove Tags (ggggeeee|..)", entry())
        self.anon_tags = ctk.CTkCheckBox(form, text="Anonymize tags via file")
        row("", self.anon_tags)
        anonbar = ctk.CTkFrame(form, fg_color="transparent")
        self.anon_file = ctk.CTkEntry(anonbar, width=360)
        self.anon_file.pack(side="left")
        ctk.CTkButton(anonbar, text="...", width=32,
                      command=lambda: self._browse_into(self.anon_file)).pack(
            side="left", padx=4)
        row("Anonymize File", anonbar)
        self.calc_dates = ctk.CTkCheckBox(form, text="Calculated dates")
        row("", self.calc_dates)
        self.img_top = row("Remove Image Top %", entry(80))
        self.img_top_mod = row("  ...top modalities", entry())
        self.img_left = row("Remove Image Left %", entry(80))
        self.img_left_mod = row("  ...left modalities", entry())
        self.morph_tags = ctk.CTkCheckBox(form, text="Enable morphing")
        row("", self.morph_tags)
        self.morph_fmt = row("Morph Format (tags|..)", entry())
        morphbar = ctk.CTkFrame(form, fg_color="transparent")
        self.morph_file = ctk.CTkEntry(morphbar, width=360)
        self.morph_file.pack(side="left")
        ctk.CTkButton(morphbar, text="...", width=32,
                      command=lambda: self._browse_into(self.morph_file)).pack(
            side="left", padx=4)
        row("Morph File", morphbar)

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=2, column=0, sticky="w", padx=PAD, pady=(0, PAD))
        self.btn = ctk.CTkButton(btnbar, text="De-identify", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(btnbar, text="Cancel",
                                        command=self._cancel, width=80,
                                        state="disabled", fg_color="#a33",
                                        hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.runner = BatchRunner(self, verb="written")
        self.runner.build(self.body).grid(row=3, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))

    def _cancel(self):
        self.runner.cancel()

    def _add_files(self):
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in chosen)
        self._update_count()

    def _add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.base_dir = Path(folder)
            self.count_lbl.configure(text="Scanning folder...")

            def work():
                return fileops.find_dicom_files(Path(folder))

            def done(found):
                self.files.extend(found)
                self.log.write(f"Added {len(found):,} file(s) from {folder}")
                self._update_count()

            run_threaded(self, work, done)

    def _on_drop(self, dropped: list):
        chosen = [Path(p) for p in dropped]
        loose = [p for p in chosen if p.is_file()]
        self.files.extend(loose)
        if loose:
            self.log.write(f"Added {len(loose):,} dropped file(s).")
        folders = [p for p in chosen if p.is_dir()]
        if folders:
            self.base_dir = folders[0]
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                return [(d, fileops.find_dicom_files(d)) for d in folders]

            def done(results):
                for d, found in results:
                    self.files.extend(found)
                    self.log.write(f"  + {d.name}:  {len(found):,} file(s)")
                self._update_count()

            run_threaded(self, work, done)
        else:
            self._update_count()

    def _pick_out(self):
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.log.write(f"Output: {folder}")

    def _browse_into(self, entry):
        f = filedialog.askopenfilename(filetypes=[("Text", "*.txt"),
                                                  ("All", "*.*")])
        if f:
            entry.delete(0, "end")
            entry.insert(0, f)

    def _update_count(self):
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")

    def requeue(self, requeue_paths: list):
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

    def _cfg(self) -> ReceiverConfig:
        def to_int(s, d=0):
            try:
                return int(str(s).strip())
            except ValueError:
                return d
        return ReceiverConfig(
            remove_private_tags=bool(self.rm_private.get()),
            remove_groups=_split(self.rm_groups.get()),
            remove_tags=_split(self.rm_tags.get()),
            anonymize_tags=bool(self.anon_tags.get()),
            anonymize_file=self.anon_file.get().strip(),
            calculated_dates=bool(self.calc_dates.get()),
            remove_image_top=to_int(self.img_top.get(), 0),
            remove_image_top_modality=_split(self.img_top_mod.get()),
            remove_image_left=to_int(self.img_left.get(), 0),
            remove_image_left_modality=_split(self.img_left_mod.get()),
            morph_tags=bool(self.morph_tags.get()),
            morphing_file_format=self.morph_fmt.get().strip(),
            morphing_file=self.morph_file.get().strip(),
        )

    def _run(self):
        if not self.files:
            self.log.write("No files selected.")
            return
        out = self.out_dir or (self.files[0].parent / "deidentified")
        cfg = self._cfg()
        files = list(self.files)
        base = self.base_dir
        total = len(files)
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.write(f"--- De-identifying {total:,} file(s) -> {out} ---")

        def worker():
            return fileops.deidentify_files(
                files, cfg, out, base_dir=base, progress=self.progress,
                on_item=self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled)

        def on_done():
            self.btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")

        self.runner.run(total, "deident", worker, on_done=on_done,
                        folder_totals=_folder_totals(files))
