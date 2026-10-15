"""Image preview: render a DICOM object's pixels to a thumbnail for a quick
visual spot-check (grayscale or color, multi-frame scrubbing) - fully offline,
using the bundled decoders. Useful for ophthalmic OP/OPT where web viewers are
unreliable."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from pydicom import dcmread

from ..tools import render
from .base import ToolPanel
from .theme import MUTED
from .widgets import run_threaded, PAD

_MAX = 512   # largest preview dimension


class PreviewPanel(ToolPanel):
    title = "Preview"
    description = ("Render a DICOM image to confirm its pixels display "
                  "correctly (grayscale/color, multi-frame).")

    def build(self) -> None:
        self._ds = None
        self._frames = 1
        self._img_ref = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)
        self.log.grid_remove()

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Open DICOM file...",
                      command=self._open).pack(side="left")
        self.drop_hint = ctk.CTkLabel(bar, text="…or drag a DICOM file here",
                                      text_color=MUTED)
        self.drop_hint.pack(side="left", padx=8)
        if not self.app.enable_drop(bar, self._on_drop):
            self.drop_hint.configure(text="")

        self.info = ctk.CTkLabel(self.body, text="No file loaded.", anchor="w",
                                 text_color=MUTED, justify="left")
        self.info.grid(row=1, column=0, sticky="w", padx=PAD)

        self.image_lbl = ctk.CTkLabel(self.body, text="")
        self.image_lbl.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=PAD)

        fb = ctk.CTkFrame(self.body, fg_color="transparent")
        fb.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.frame_lbl = ctk.CTkLabel(fb, text="", text_color=MUTED, width=120)
        self.frame_lbl.pack(side="left")
        self.frame_slider = ctk.CTkSlider(fb, from_=0, to=1,
                                          command=self._on_frame)
        self._fb = fb

    # -- load ------------------------------------------------------------
    def _on_drop(self, dropped: list) -> None:
        files = [Path(p) for p in dropped if Path(p).is_file()]
        if files:
            self._load(files[0])

    def _open(self) -> None:
        p = filedialog.askopenfilename(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if p:
            self._load(Path(p))

    def _load(self, path: Path) -> None:
        self.info.configure(text=f"Loading {path.name} …", text_color=MUTED)

        def work():
            return dcmread(str(path), force=True)

        def done(ds):
            self._ds = ds
            self._frames = render.frame_count(ds)
            if self._frames > 1:
                self.frame_slider.configure(
                    to=self._frames - 1, number_of_steps=self._frames - 1)
                self.frame_slider.set(0)
                self.frame_slider.pack(side="left", padx=PAD, fill="x",
                                       expand=True)
            else:
                self.frame_slider.pack_forget()
            self._show_frame(0)

        def err(exc, tb):
            self.info.configure(text=f"[ERROR] {exc}", text_color="#d9695f")

        run_threaded(self, work, done, err)

    def _on_frame(self, value) -> None:
        self._show_frame(int(float(value)))

    def _show_frame(self, frame: int) -> None:
        ds = self._ds
        if ds is None:
            return
        self.frame_lbl.configure(
            text=f"Frame {frame + 1}/{self._frames}" if self._frames > 1 else "")

        def work():
            return render.render_image(ds, frame)

        def done(img):
            w, h = img.size
            scale = min(_MAX / w, _MAX / h, 1.0)
            size = (max(1, int(w * scale)), max(1, int(h * scale)))
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._img_ref = cimg
            self.image_lbl.configure(image=cimg, text="")
            ts = ""
            fm = getattr(ds, "file_meta", None)
            if fm is not None:
                ts = str(getattr(fm.TransferSyntaxUID, "name", ""))
            self.info.configure(
                text=f"{getattr(ds, 'Modality', '?')}   ·   {w}×{h}   ·   "
                     f"{self._frames} frame(s)   ·   "
                     f"{getattr(ds, 'PhotometricInterpretation', '')}   ·   {ts}",
                text_color=MUTED)

        def err(exc, tb):
            self._img_ref = None
            self.image_lbl.configure(image=None, text=f"Cannot render: {exc}")

        run_threaded(self, work, done, err)
