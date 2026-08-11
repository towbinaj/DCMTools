"""File tool panels: Tag List, Modify, Split Multiframe, Dump."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..tools import fileops
from .base import ToolPanel
from .widgets import run_threaded, PAD


class TagListPanel(ToolPanel):
    title = "Tag Lister"
    description = "Inspect the DICOM header of a single file."

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Open DICOM file...",
                      command=self._open).pack(side="left")
        self.path_lbl = ctk.CTkLabel(bar, text="", text_color="gray")
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


class ModifyPanel(ToolPanel):
    title = "Modify Header"
    description = ("Set or remove tags across files. Tags as ggggeeee "
                   "(e.g. 00100010).")

    def build(self) -> None:
        self.files: list[Path] = []
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=4, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Add Files...",
                      command=self._add_files).pack(side="left")
        ctk.CTkButton(bar, text="Add Folder...",
                      command=self._add_folder).pack(side="left", padx=PAD)
        self.count_lbl = ctk.CTkLabel(bar, text="No files.", text_color="gray")
        self.count_lbl.pack(side="left", padx=PAD)

        opsframe = ctk.CTkFrame(self.body)
        opsframe.grid(row=1, column=0, columnspan=4, sticky="ew",
                      padx=PAD, pady=PAD)
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

        self.in_place = ctk.CTkCheckBox(self.body,
                                        text="Overwrite files in place "
                                             "(otherwise write to ./modified)")
        self.in_place.grid(row=2, column=0, columnspan=4, sticky="w",
                           padx=PAD, pady=PAD)

        self.btn = ctk.CTkButton(self.body, text="Apply", command=self._run)
        self.btn.grid(row=3, column=0, sticky="w", padx=PAD, pady=(0, PAD))

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in paths)
        self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.files.extend(fileops.find_dicom_files(Path(folder)))
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files)} file(s).")

    def _run(self) -> None:
        if not self.files:
            self.log.write("No files selected.")
            return
        ops = []
        for action, tag, val in self.op_rows:
            t = tag.get().strip()
            if not t:
                continue
            ops.append(fileops.ModifyOp(tag=t, action=action.get(),
                                        value=val.get()))
        if not ops:
            self.log.write("No operations defined.")
            return
        self.btn.configure(state="disabled")
        self.log.write(f"--- Modifying {len(self.files)} file(s) ---")
        files = list(self.files)
        in_place = bool(self.in_place.get())

        def work():
            return fileops.modify_files(files, ops, in_place=in_place,
                                        progress=self.progress)

        def done(result):
            self.log.write(f"[DONE] changed={result.changed} "
                           f"failed={result.failed}")
            for e in result.errors[:20]:
                self.log.write(f"   {e}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)


class SplitPanel(ToolPanel):
    title = "Split Multiframe"
    description = "Split multi-frame DICOM objects into single-frame files."

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Add Files...",
                      command=self._add_files).pack(side="left")
        ctk.CTkButton(bar, text="Add Folder...",
                      command=self._add_folder).pack(side="left", padx=PAD)
        self.count_lbl = ctk.CTkLabel(bar, text="No files.", text_color="gray")
        self.count_lbl.pack(side="left", padx=PAD)

        outbar = ctk.CTkFrame(self.body, fg_color="transparent")
        outbar.grid(row=1, column=0, sticky="ew", padx=PAD)
        ctk.CTkButton(outbar, text="Output folder...",
                      command=self._pick_out).pack(side="left")
        self.out_lbl = ctk.CTkLabel(outbar, text="(not set)",
                                    text_color="gray")
        self.out_lbl.pack(side="left", padx=PAD)

        self.btn = ctk.CTkButton(self.body, text="Split", command=self._run)
        self.btn.grid(row=2, column=0, sticky="w", padx=PAD, pady=PAD)

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in paths)
        self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.files.extend(fileops.find_dicom_files(Path(folder)))
        self._update_count()

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=folder)

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files)} file(s).")

    def _run(self) -> None:
        if not self.files:
            self.log.write("No files selected.")
            return
        out = self.out_dir or (self.files[0].parent / "split")
        self.btn.configure(state="disabled")
        self.log.write(f"--- Splitting into {out} ---")
        files = list(self.files)

        def work():
            return fileops.split_multiframe(files, out, progress=self.progress)

        def done(result):
            self.log.write(f"[DONE] frames={result.frames_written} "
                           f"processed={result.files_processed} "
                           f"skipped={result.skipped}")
            for e in result.errors[:20]:
                self.log.write(f"   {e}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)


class DumpPanel(ToolPanel):
    title = "Folder Dump"
    description = "Scan a folder and export study/exam metadata to CSV."

    def build(self) -> None:
        self.root: Path | None = None
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Choose folder...",
                      command=self._pick).pack(side="left")
        self.root_lbl = ctk.CTkLabel(bar, text="(not set)", text_color="gray")
        self.root_lbl.pack(side="left", padx=PAD)

        self.recursive = ctk.CTkCheckBox(self.body, text="Recurse subfolders")
        self.recursive.select()
        self.recursive.grid(row=1, column=0, sticky="w", padx=PAD, pady=PAD)

        self.btn = ctk.CTkButton(self.body, text="Scan + Export CSV",
                                 command=self._run)
        self.btn.grid(row=2, column=0, sticky="w", padx=PAD, pady=(0, PAD))
        self._result = None

    def _pick(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.root = Path(folder)
            self.root_lbl.configure(text=folder)

    def _run(self) -> None:
        if not self.root:
            self.log.write("Choose a folder first.")
            return
        self.btn.configure(state="disabled")
        root = self.root
        recursive = bool(self.recursive.get())
        self.log.write(f"--- Dumping {root} ---")

        def work():
            return fileops.dump_folder(root, recursive=recursive,
                                       progress=self.progress)

        def done(result):
            self._result = result
            self.log.write(f"[DONE] {result.files_read} file(s) read.")
            if result.rows:
                out = filedialog.asksaveasfilename(
                    defaultextension=".csv",
                    filetypes=[("CSV", "*.csv")],
                    initialfile="dump.csv")
                if out:
                    fileops.write_dump_csv(result, Path(out))
                    self.log.write(f"Saved CSV: {out}")
            self.btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.btn.configure(state="normal")

        run_threaded(self, work, done, err)
