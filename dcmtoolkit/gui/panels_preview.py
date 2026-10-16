"""Image preview: render DICOM pixels to a thumbnail for a quick visual
spot-check (grayscale or color). Scrubs a whole series - every instance (and
every frame of multi-frame instances) on one slider - fully offline via the
bundled decoders. Useful for ophthalmic OP/OPT where web viewers are
unreliable."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from pydicom import dcmread

from ..tools import render
from ..tools.fileops import expand_sources
from .base import ToolPanel
from .theme import MUTED
from .widgets import run_threaded, PAD

_MAX = 512        # largest preview dimension
_CACHE_MAX = 6    # number of fully-read datasets to keep in memory


class PreviewPanel(ToolPanel):
    title = "Preview"
    description = ("Render DICOM images to confirm pixels display correctly - "
                  "one image or scrub a whole series (grayscale/color).")

    def build(self) -> None:
        # A "view" is one (file, frame) pair; the slider scrubs across all
        # views of the loaded series in order.
        self._views: list[tuple[Path, int]] = []
        self._cache: "OrderedDict[Path, object]" = OrderedDict()
        self._img_ref = None
        self._idx = 0

        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)
        self.log.grid_remove()

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Open file...", command=self._open).pack(
            side="left")
        ctk.CTkButton(bar, text="Open series folder...",
                      command=self._open_folder).pack(side="left", padx=PAD)

        self.info = ctk.CTkLabel(self.body, text="No image loaded.", anchor="w",
                                 text_color=MUTED, justify="left")
        self.info.grid(row=1, column=0, sticky="w", padx=PAD)

        # The whole central area is the drop zone: a bordered frame that fills
        # the panel, with a clear placeholder and a drag-over highlight, so it's
        # obvious where to drop a file or a series folder.
        self._drop_border = ("gray60", "gray45")
        self.drop = ctk.CTkFrame(self.body, border_width=2,
                                 border_color=self._drop_border,
                                 corner_radius=10)
        self.drop.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.drop.grid_rowconfigure(0, weight=1)
        self.drop.grid_columnconfigure(0, weight=1)
        self.image_lbl = ctk.CTkLabel(
            self.drop, text="↓   Drop a DICOM file or a series folder here",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=MUTED)
        self.image_lbl.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)

        self._drop_ok = self.app.enable_drop(
            self.drop, self._on_drop,
            on_enter=self._drop_enter, on_leave=self._drop_leave)
        self.app.enable_drop(self.image_lbl, self._on_drop,
                             on_enter=self._drop_enter, on_leave=self._drop_leave)
        if not self._drop_ok:
            self.image_lbl.configure(
                text="Open a DICOM file or a series folder using the buttons "
                     "above\n(drag-and-drop unavailable on this system)")
        # Mouse-wheel scrolls through the series (Windows/Mac delta + X11 buttons).
        for target in (self.image_lbl, self.drop, self.info):
            target.bind("<MouseWheel>", self._on_wheel)
            target.bind("<Button-4>", self._on_wheel)
            target.bind("<Button-5>", self._on_wheel)

        fb = ctk.CTkFrame(self.body, fg_color="transparent")
        fb.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.pos_lbl = ctk.CTkLabel(fb, text="", text_color=MUTED, width=240,
                                    anchor="w")
        self.pos_lbl.pack(side="left")
        self.slider = ctk.CTkSlider(fb, from_=0, to=1, command=self._on_slide)
        self._fb = fb

    # -- load ------------------------------------------------------------
    def _on_drop(self, dropped: list) -> None:
        roots = [Path(p) for p in dropped]
        if not roots:
            return
        # A single dropped file -> show just it; a folder (or several) -> series.
        if len(roots) == 1 and roots[0].is_file():
            self._load_series(roots, single=True)
        else:
            self._load_series(roots)

    def _open(self) -> None:
        p = filedialog.askopenfilename(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if p:
            self._load_series([Path(p)], single=True)

    def _open_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select a series / study folder")
        if folder:
            self._load_series([Path(folder)])

    def _load_series(self, roots: list[Path], single: bool = False) -> None:
        self.info.configure(text="Scanning …", text_color=MUTED)
        self._cache.clear()

        def work():
            files = roots if single else expand_sources(roots)
            # Read headers only (no pixels) to group by series and order.
            entries = []
            for f in files:
                try:
                    ds = dcmread(str(f), stop_before_pixels=True, force=True)
                except Exception:  # noqa: BLE001 - skip non-DICOM
                    continue
                suid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                inst = getattr(ds, "InstanceNumber", None)
                try:
                    inst = int(inst)
                except (TypeError, ValueError):
                    inst = 10**9   # unnumbered -> sort last
                entries.append((suid, inst, str(f), render.frame_count(ds),
                                str(getattr(ds, "SeriesDescription", "") or ""),
                                str(getattr(ds, "Modality", "") or "")))
            if not entries:
                raise ValueError("No DICOM images found.")
            groups = defaultdict(list)
            for suid, inst, f, nf, desc, mod in entries:
                groups[suid].append((inst, f, nf))
            # Pick the largest series (most instances); ties keep first seen.
            best_suid = max(groups, key=lambda k: len(groups[k]))
            best = sorted(groups[best_suid], key=lambda t: (t[0], t[1]))
            views = []
            for inst, f, nf in best:
                for fr in range(max(1, nf)):
                    views.append((Path(f), fr))
            meta = next((e for e in entries if e[0] == best_suid), None)
            return views, len(groups), len(best), meta

        def done(result):
            views, ngroups, ninst, meta = result
            self._views = views
            n = len(views)
            if n > 1:
                self.slider.configure(to=n - 1, number_of_steps=n - 1)
                self.slider.set(0)
                self.slider.pack(side="left", padx=PAD, fill="x", expand=True)
            else:
                self.slider.pack_forget()
            extra = ""
            if ngroups > 1:
                extra = f"  (largest of {ngroups} series shown)"
            desc = (meta[4] if meta else "") or ""
            self.info.configure(
                text=f"Series: {ninst} image(s), {n} view(s){extra}"
                     + (f"  ·  {desc}" if desc else ""),
                text_color=MUTED)
            self._show_view(0)

        def err(exc, tb):
            self._views = []
            self.info.configure(text=f"[ERROR] {exc}", text_color="#d9695f")
            self.image_lbl.configure(image=None, text="")

        run_threaded(self, work, done, err)

    def _drop_enter(self) -> None:
        self.drop.configure(border_color="#4f9dde")
        if not self._views:
            self.image_lbl.configure(text="↓   Release to open")

    def _drop_leave(self) -> None:
        self.drop.configure(border_color=self._drop_border)
        if not self._views:
            self.image_lbl.configure(
                text="↓   Drop a DICOM file or a series folder here")

    # -- display ---------------------------------------------------------
    def _on_slide(self, value) -> None:
        self._show_view(int(float(value)))

    def _on_wheel(self, event) -> str:
        """Scroll through the series with the mouse wheel."""
        if len(self._views) < 2:
            return "break"
        # Wheel up (delta > 0, or X11 Button-4) -> previous image.
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        new = max(0, min(self._idx + step, len(self._views) - 1))
        if new != self._idx:
            self.slider.set(new)
            self._show_view(new)
        return "break"

    def _read_ds(self, path: Path):
        ds = self._cache.get(path)
        if ds is None:
            ds = dcmread(str(path), force=True)
            self._cache[path] = ds
            while len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(path)
        return ds

    def _show_view(self, idx: int) -> None:
        if not self._views:
            return
        idx = max(0, min(idx, len(self._views) - 1))
        self._idx = idx
        path, frame = self._views[idx]
        total = len(self._views)
        pos = f"{idx + 1} / {total}" if total > 1 else "1 image"

        def work():
            ds = self._read_ds(path)
            return ds, render.render_image(ds, frame)

        def done(result):
            ds, img = result
            w, h = img.size
            scale = min(_MAX / w, _MAX / h, 1.0)
            size = (max(1, int(w * scale)), max(1, int(h * scale)))
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._img_ref = cimg
            self.image_lbl.configure(image=cimg, text="")
            pm = getattr(ds, "PhotometricInterpretation", "")
            self.pos_lbl.configure(text=f"{pos}   ·   {w}×{h}   ·   {pm}")

        def err(exc, tb):
            self._img_ref = None
            self.image_lbl.configure(image=None, text=f"Cannot render: {exc}")

        run_threaded(self, work, done, err)
