"""Store Receiver panel: run the C-STORE SCP in-app or as a Windows service."""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config
from ..net.scp import StoreSCP
from ..store.naming import FORMATS
from ..store.processing import ReceiverConfig
from .base import ToolPanel
from .theme import MUTED
from .widgets import run_threaded, PAD


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split("|") if p.strip()]


def _elevate_run(args: list[str]) -> None:
    """Relaunch this program elevated with the given args (Windows UAC)."""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, " ".join(f'"{a}"' for a in args)
    else:
        exe = sys.executable
        script = str(Path(__file__).resolve().parents[2] / "main.py")
        params = " ".join(f'"{a}"' for a in [script, *args])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)


class ReceiverPanel(ToolPanel):
    title = "Store Receiver"
    description = ("Receive DICOM (C-STORE SCP), save to disk with optional "
                  "de-identification. Run in-app or as a Windows service.")

    def build(self) -> None:
        self.scp: StoreSCP | None = None
        cfg = config.load_receiver_config()

        # Let the config form fill the panel; keep the log a short strip.
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.log.configure(height=90)
        self.body.grid_configure(sticky="nsew")
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        form = ctk.CTkScrollableFrame(self.body)
        form.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        form.grid_columnconfigure(1, weight=1)
        r = 0

        def row(label, widget):
            nonlocal r
            ctk.CTkLabel(form, text=label, anchor="w", width=170).grid(
                row=r, column=0, sticky="w", padx=PAD, pady=3)
            widget.grid(row=r, column=1, sticky="ew", padx=PAD, pady=3)
            r += 1
            return widget

        def entry(val="", width=280):
            e = ctk.CTkEntry(form, width=width)
            if val not in ("", None):
                e.insert(0, str(val))
            return e

        # [DICOM]
        ctk.CTkLabel(form, text="DICOM", font=ctk.CTkFont(weight="bold")).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=(4, 0)); r += 1
        self.ae = row("AE Title", entry(cfg.aetitle))
        self.port = row("Port", entry(cfg.port, width=100))
        savebar = ctk.CTkFrame(form, fg_color="transparent")
        self.save_folder = ctk.CTkEntry(savebar, width=360)
        if cfg.save_folder:
            self.save_folder.insert(0, cfg.save_folder)
        self.save_folder.pack(side="left")
        ctk.CTkButton(savebar, text="...", width=32,
                      command=self._browse_save).pack(side="left", padx=4)
        row("Save Folder", savebar)
        self.fmt = ctk.CTkOptionMenu(form, values=FORMATS)
        self.fmt.set(cfg.folder_format if cfg.folder_format in FORMATS
                     else "PATIENT")
        row("Folder Format", self.fmt)

        # TLS (secure receiver)
        ctk.CTkLabel(form, text="TLS (secure receiver)",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=(8, 0)); r += 1
        self.tls = ctk.CTkCheckBox(form, text="Require TLS on this listener")
        if cfg.tls:
            self.tls.select()
        row("", self.tls)
        certbar = ctk.CTkFrame(form, fg_color="transparent")
        self.tls_cert = ctk.CTkEntry(certbar, width=360)
        if cfg.tls_cert_file:
            self.tls_cert.insert(0, cfg.tls_cert_file)
        self.tls_cert.pack(side="left")
        ctk.CTkButton(certbar, text="...", width=32,
                      command=lambda: self._browse_into(self.tls_cert)).pack(
            side="left", padx=4)
        row("Server cert (.pem)", certbar)
        keybar = ctk.CTkFrame(form, fg_color="transparent")
        self.tls_key = ctk.CTkEntry(keybar, width=360)
        if cfg.tls_key_file:
            self.tls_key.insert(0, cfg.tls_key_file)
        self.tls_key.pack(side="left")
        ctk.CTkButton(keybar, text="...", width=32,
                      command=lambda: self._browse_into(self.tls_key)).pack(
            side="left", padx=4)
        row("Server key (.pem)", keybar)
        cabar = ctk.CTkFrame(form, fg_color="transparent")
        self.tls_ca = ctk.CTkEntry(cabar, width=360)
        if cfg.tls_ca_file:
            self.tls_ca.insert(0, cfg.tls_ca_file)
        self.tls_ca.pack(side="left")
        ctk.CTkButton(cabar, text="...", width=32,
                      command=lambda: self._browse_into(self.tls_ca)).pack(
            side="left", padx=4)
        row("Client CA (optional)", cabar)
        self.require_client = ctk.CTkCheckBox(
            form, text="Require client certificate (mutual TLS)")
        if cfg.require_client_cert:
            self.require_client.select()
        row("", self.require_client)

        # [ANONYMIZE]
        ctk.CTkLabel(form, text="Anonymize",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=(8, 0)); r += 1
        self.rm_private = ctk.CTkCheckBox(form, text="Remove private tags")
        if cfg.remove_private_tags:
            self.rm_private.select()
        row("", self.rm_private)
        self.rm_groups = row("Remove Groups (gggg|..)",
                             entry("|".join(cfg.remove_groups)))
        self.rm_tags = row("Remove Tags (ggggeeee|..)",
                           entry("|".join(cfg.remove_tags)))
        self.anon_tags = ctk.CTkCheckBox(form, text="Anonymize tags via file")
        if cfg.anonymize_tags:
            self.anon_tags.select()
        row("", self.anon_tags)
        anonbar = ctk.CTkFrame(form, fg_color="transparent")
        self.anon_file = ctk.CTkEntry(anonbar, width=360)
        if cfg.anonymize_file:
            self.anon_file.insert(0, cfg.anonymize_file)
        self.anon_file.pack(side="left")
        ctk.CTkButton(anonbar, text="...", width=32,
                      command=lambda: self._browse_into(self.anon_file)).pack(
            side="left", padx=4)
        row("Anonymize File", anonbar)
        self.calc_dates = ctk.CTkCheckBox(
            form, text="Calculated dates (baseline 19000101)")
        if cfg.calculated_dates:
            self.calc_dates.select()
        row("", self.calc_dates)
        self.img_top = row("Remove Image Top %",
                           entry(cfg.remove_image_top, width=80))
        self.img_top_mod = row("  ...top modalities",
                               entry("|".join(cfg.remove_image_top_modality)))
        self.img_left = row("Remove Image Left %",
                            entry(cfg.remove_image_left, width=80))
        self.img_left_mod = row("  ...left modalities",
                                entry("|".join(cfg.remove_image_left_modality)))

        # [MORPH]
        ctk.CTkLabel(form, text="Morph",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=(8, 0)); r += 1
        self.morph_tags = ctk.CTkCheckBox(form, text="Enable morphing")
        if cfg.morph_tags:
            self.morph_tags.select()
        row("", self.morph_tags)
        self.morph_fmt = row("Morph Format (tags|..)",
                             entry(cfg.morphing_file_format))
        morphbar = ctk.CTkFrame(form, fg_color="transparent")
        self.morph_file = ctk.CTkEntry(morphbar, width=360)
        if cfg.morphing_file:
            self.morph_file.insert(0, cfg.morphing_file)
        self.morph_file.pack(side="left")
        ctk.CTkButton(morphbar, text="...", width=32,
                      command=lambda: self._browse_into(self.morph_file)).pack(
            side="left", padx=4)
        row("Morph File", morphbar)

        # Controls
        ctrl = ctk.CTkFrame(self.body, fg_color="transparent")
        ctrl.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.start_btn = ctk.CTkButton(ctrl, text="Start (in-app)",
                                       command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(ctrl, text="Stop", command=self._stop,
                                      state="disabled", fg_color="#a33",
                                      hover_color="#c44")
        self.stop_btn.pack(side="left", padx=PAD)
        ctk.CTkButton(ctrl, text="Save config",
                      command=self._save).pack(side="left", padx=PAD)
        self.status = ctk.CTkLabel(ctrl, text="Stopped.", text_color=MUTED)
        self.status.pack(side="left", padx=PAD)

        # Service controls
        svc = ctk.CTkFrame(self.body, fg_color="transparent")
        svc.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        ctk.CTkLabel(svc, text="Windows service:").pack(side="left")
        for label, cmd in [("Install", "install"), ("Start", "start"),
                           ("Stop", "stop"), ("Remove", "remove")]:
            ctk.CTkButton(svc, text=label, width=70,
                          command=lambda c=cmd: self._service(c)).pack(
                side="left", padx=3)

    # -- helpers ---------------------------------------------------------
    def _browse_save(self):
        d = filedialog.askdirectory()
        if d:
            self.save_folder.delete(0, "end")
            self.save_folder.insert(0, d)

    def _browse_into(self, entry):
        f = filedialog.askopenfilename(filetypes=[("Text", "*.txt"),
                                                  ("All", "*.*")])
        if f:
            entry.delete(0, "end")
            entry.insert(0, f)

    def _gather(self) -> ReceiverConfig:
        def to_int(s, d=0):
            try:
                return int(str(s).strip())
            except ValueError:
                return d
        return ReceiverConfig(
            aetitle=self.ae.get().strip() or "STORESCP",
            port=to_int(self.port.get(), 104),
            save_folder=self.save_folder.get().strip(),
            folder_format=self.fmt.get(),
            tls=bool(self.tls.get()),
            tls_cert_file=self.tls_cert.get().strip(),
            tls_key_file=self.tls_key.get().strip(),
            tls_ca_file=self.tls_ca.get().strip(),
            require_client_cert=bool(self.require_client.get()),
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

    def _save(self):
        config.save_receiver_config(self._gather())
        self.log.write("Saved receiver config to " +
                       str(config.receiver_config_path()))

    def _start(self):
        cfg = self._gather()
        config.save_receiver_config(cfg)
        try:
            self.scp = StoreSCP(cfg, on_update=self._on_update)
            self.scp.start()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Start failed", str(exc))
            self.log.write(f"[ERROR] {exc}")
            return
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.log.write(f"Listening on port {cfg.port} as {cfg.aetitle} "
                       f"(format={cfg.folder_format}).")

    def _stop(self):
        if self.scp:
            self.scp.stop()
            self.scp = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="Stopped.")
        self.log.write("Receiver stopped.")

    def _on_update(self, stats):
        # Called from the SCP thread; marshal to the UI thread.
        def apply():
            up = int(time.time() - stats.started_at) if stats.started_at else 0
            self.status.configure(
                text=f"Listening | received {stats.received} | "
                     f"failed {stats.failed} | up {up}s")
            if stats.recent:
                t, summary, rel = stats.recent[0]
                self.log.write(f"[{t}] {summary}  ->  {rel}")
        self.after(0, apply)

    def _service(self, cmd: str):
        if sys.platform != "win32":
            messagebox.showinfo("Service", "Windows only.")
            return
        # Persist config so the service reads the same settings.
        config.save_receiver_config(self._gather())
        if not messagebox.askyesno(
                "Windows service",
                f"Run 'service {cmd}' elevated (UAC prompt)?"):
            return
        try:
            _elevate_run(["service", cmd])
            self.log.write(f"Requested service {cmd} (elevated).")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Service", str(exc))
