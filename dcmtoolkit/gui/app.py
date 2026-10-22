"""Main application window: sidebar navigation + swappable tool panels."""

from __future__ import annotations

import threading

import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except Exception:  # noqa: BLE001 - drag-drop is optional
    TkinterDnD = None
    DND_FILES = None
    _HAS_DND = False

from .. import APP_NAME, __version__, config, paths
from ..logging_setup import setup as setup_logging
from .panels_network import (EchoPanel, SendPanel, QueryMovePanel,
                             EchoAllPanel, RetrievePanel, StorageCommitPanel)
from .panels_files import (TagListPanel, ModifyPanel, SplitPanel, DumpPanel,
                           DeidentifyPanel, EnhancedPanel)
from .panels_preview import PreviewPanel
from .panels_compare import ComparePanel
from .panels_edit import PatientEditPanel, StudyEditPanel, SeriesEditPanel
from .panels_modality import ModalitySCUPanel
from .panels_hl7 import HL7SenderPanel
from .panels_qa import QAPanel
from .panels_settings import SettingsPanel
from .panels_logs import LogPanel
from .theme import MUTED, apply_scale, tool_color

# Store receiver panel is optional (imports pywin32 lazily for service bits).
try:
    from .panels_receiver import ReceiverPanel
    _HAS_RECEIVER = True
except Exception:  # noqa: BLE001
    _HAS_RECEIVER = False


NAV_GROUPS = [
    ("Network", [EchoPanel, EchoAllPanel, SendPanel, QueryMovePanel,
                 RetrievePanel, StorageCommitPanel]),
    ("Files", [TagListPanel, PreviewPanel, ComparePanel, ModifyPanel,
               SplitPanel, EnhancedPanel, DumpPanel, DeidentifyPanel, QAPanel]),
    ("Edit demographics", [PatientEditPanel, StudyEditPanel, SeriesEditPanel]),
    ("Modality", [ModalitySCUPanel]),
    ("HL7", [HL7SenderPanel]),
]


_APP_BASES = (ctk.CTk, TkinterDnD.DnDWrapper) if _HAS_DND else (ctk.CTk,)


class App(*_APP_BASES):
    def __init__(self):
        super().__init__()
        self._dnd_ok = False
        if _HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
            except Exception:  # noqa: BLE001
                self._dnd_ok = False
        setup_logging()
        self.settings = config.load_settings()
        self.destinations = config.load_destinations()

        ctk.set_appearance_mode(self.settings.appearance)
        ctk.set_default_color_theme("blue")
        apply_scale(self.settings.ui_scale)

        self.title(f"{APP_NAME}  v{__version__}")
        self.minsize(860, 580)
        # Restore the previous window size/position, if remembered and on-screen.
        if self.settings.window_geometry and self._geometry_on_screen(
                self.settings.window_geometry):
            self.geometry(self.settings.window_geometry)
        else:
            self.geometry("1040x720")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        icon = paths.resource_dir() / "assets" / "icon.ico"
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:  # noqa: BLE001 - non-fatal on some platforms
                pass

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar --------------------------------------------------------
        self.sidebar = ctk.CTkScrollableFrame(self, width=212,
                                              corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="DICOM\nToolkit",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     justify="left").pack(anchor="w", padx=14, pady=(14, 10))

        # Content --------------------------------------------------------
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.panels: dict[str, ctk.CTkFrame] = {}      # built lazily on first open
        self._panel_classes: dict[str, type] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._build_nav()

        # Status bar -----------------------------------------------------
        status = ctk.CTkFrame(self, height=24, corner_radius=0)
        status.grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(status, text=f"Data: {paths.data_dir()}",
                     text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=8)
        self.ae_status = ctk.CTkLabel(
            status, text=f"My AE: {self.settings.my_aetitle}",
            text_color=MUTED, font=ctk.CTkFont(size=11))
        self.ae_status.pack(side="right", padx=8)
        ctk.CTkButton(status, text="Report a Bug", height=20, width=100,
                      fg_color="#b5651d", hover_color="#c8761f",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=self._report_bug).pack(side="right", padx=6)
        # Update notice (hidden until an update is found).
        self._update_info = None
        self.update_btn = ctk.CTkButton(
            status, text="", height=20, width=0, fg_color="#2e8b57",
            hover_color="#3fa06a", font=ctk.CTkFont(size=11, weight="bold"),
            command=self._start_update)

        # Restore the last-used tool, else show the first panel.
        self._current = ""
        if self._panel_classes:
            last = self.settings.last_tool
            self.select(last if last in self._panel_classes
                        else next(iter(self._panel_classes)))

        # Quiet background update check (built exe only).
        from .. import updater
        if updater.can_self_update():
            self.after(2500, lambda: self._check_updates_async(manual=False))

    # -- self-update -----------------------------------------------------
    def _check_updates_async(self, manual: bool = False) -> None:
        from .. import updater

        def work():
            info = updater.check_latest()
            try:
                self.after(0, lambda: self._on_update_result(info, manual))
            except RuntimeError:
                pass   # window closed before the check returned

        threading.Thread(target=work, daemon=True).start()

    def _report_bug(self) -> None:
        from .bugreport import BugReportDialog
        BugReportDialog(self)

    def _on_update_result(self, info, manual: bool) -> None:
        from tkinter import messagebox
        from .. import updater
        self._update_info = info
        if info:
            self.update_btn.configure(text=f"⬆ Update to v{info.version}")
            self.update_btn.pack(side="right", padx=8)
            if manual and messagebox.askyesno(
                    "Update available",
                    f"Version {info.version} is available "
                    f"(you have {updater.current_version()}).\n\nInstall now? "
                    "The app will restart; your settings are kept."):
                self._start_update()
        elif manual:
            messagebox.showinfo(
                "Up to date",
                f"You are on the latest version ({updater.current_version()}).")

    def _start_update(self) -> None:
        from tkinter import messagebox
        from .. import updater
        info = self._update_info
        if not info:
            return
        if not updater.can_self_update():
            messagebox.showinfo(
                "Update", f"Version {info.version} is available. Rebuild the "
                "exe from source to update (self-update needs the built exe).")
            return
        if not messagebox.askyesno(
                "Install update",
                f"Download and install v{info.version}? The app will close and "
                "reopen on the new version. Your settings are preserved."):
            return

        top = ctk.CTkToplevel(self)
        top.title("Updating")
        top.geometry("360x110")
        top.transient(self)
        ctk.CTkLabel(top, text=f"Downloading v{info.version}…").pack(pady=(18, 6))
        bar = ctk.CTkProgressBar(top, width=300)
        bar.set(0)
        bar.pack(pady=6)

        def prog(frac):
            self.after(0, lambda: bar.set(frac))

        def work():
            try:
                updater.apply_update(info, progress=prog)
                self.after(0, self._finish_update)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._update_failed(top, e))

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self) -> None:
        # The swap helper is waiting for us to exit, then relaunches.
        self._on_close()

    def _update_failed(self, top, exc) -> None:
        from tkinter import messagebox
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        messagebox.showerror("Update failed", str(exc))

    def _build_nav(self) -> None:
        groups = list(NAV_GROUPS)
        receiver_group = []
        if _HAS_RECEIVER:
            receiver_group = [("Receiver", [ReceiverPanel])]
        groups = groups + receiver_group + [("Config", [LogPanel,
                                                        SettingsPanel])]

        for group_name, panel_classes in groups:
            ctk.CTkLabel(self.sidebar, text=group_name.upper(),
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=MUTED).pack(anchor="w", padx=14,
                                                 pady=(12, 2))
            for cls in panel_classes:
                key = cls.__name__
                self._panel_classes[key] = cls   # instantiated on first select()

                item = ctk.CTkFrame(self.sidebar, fg_color="transparent")
                item.pack(fill="x", padx=8, pady=2)
                # per-tool color accent bar
                ctk.CTkFrame(item, width=5, height=30, corner_radius=3,
                             fg_color=tool_color(key)).pack(side="left",
                                                            padx=(0, 6))
                btn = ctk.CTkButton(
                    item, text=cls.title, anchor="w", height=34,
                    font=ctk.CTkFont(size=14),
                    fg_color="transparent", text_color=("gray10", "gray90"),
                    hover_color=("gray75", "gray25"),
                    command=lambda k=key: self.select(k))
                btn.pack(side="left", fill="x", expand=True)
                self._buttons[key] = btn

    def _ensure_panel(self, key: str):
        """Build a panel the first time it's needed. All panels are stacked in
        the same cell; a new one is lowered behind the current so it renders
        hidden (no flash, no field-by-field painting). Switching later is a
        pure z-order change (tkraise) - no unmap/remap, so even heavy panels
        like Echo-All don't re-render when you return to them."""
        panel = self.panels.get(key)
        if panel is not None:
            return panel
        cls = self._panel_classes.get(key)
        if cls is None:
            return None
        panel = cls(self.content, self)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.lower()              # render behind the current panel
        self.panels[key] = panel
        return panel

    def select(self, key: str) -> None:
        self._current = key
        panel = self._ensure_panel(key)
        if panel is None:
            return
        panel.tkraise()            # bring to front; no re-render of its widgets
        if hasattr(panel, "on_show"):
            panel.on_show()
        for k, btn in self._buttons.items():
            btn.configure(fg_color=("gray80", "gray28") if k == key
                          else "transparent")

    def _geometry_on_screen(self, geo: str) -> bool:
        """True if a saved 'WxH+X+Y' would open (mostly) on the current screen."""
        import re
        m = re.match(r"\d+x\d+([+-]\d+)([+-]\d+)$", geo)
        if not m:
            return False
        x, y = int(m.group(1)), int(m.group(2))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        return -50 <= x <= sw - 100 and -20 <= y <= sh - 100

    def _on_close(self) -> None:
        # Remember the window size/position for next launch.
        try:
            self.settings.window_geometry = self.geometry()
            self.settings.last_tool = getattr(self, "_current", "")
            config.save_settings(self.settings)
        except Exception:  # noqa: BLE001
            pass
        # Stop the receiver SCP if it's still listening, so the process exits.
        recv = self.panels.get("ReceiverPanel")
        if recv is not None and getattr(recv, "scp", None):
            try:
                recv.scp.stop()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()

    def enable_drop(self, widget, on_paths, on_enter=None, on_leave=None) -> bool:
        """Register a widget as a file/folder drop target.

        ``on_paths`` receives a list of dropped filesystem paths (strings).
        ``on_enter``/``on_leave`` fire while a drag hovers, for visual feedback.
        Returns False if drag-and-drop is unavailable.
        """
        if not self._dnd_ok:
            return False
        try:
            widget.drop_target_register(DND_FILES)

            def _drop(e):
                if on_leave:
                    on_leave()
                on_paths(list(self.tk.splitlist(e.data)))

            widget.dnd_bind("<<Drop>>", _drop)
            if on_enter:
                widget.dnd_bind("<<DropEnter>>", lambda e: on_enter())
            if on_leave:
                widget.dnd_bind("<<DropLeave>>", lambda e: on_leave())
            return True
        except Exception:  # noqa: BLE001
            return False

    def remember_destination(self, key: str, name: str) -> None:
        """Persist the last-used destination for a given tool."""
        if self.settings.last_destinations.get(key) != name:
            self.settings.last_destinations[key] = name
            config.save_settings(self.settings)

    def tls_args_for(self, node):
        """Build pynetdicom tls_args for a destination, or None if plain TCP."""
        if not getattr(node, "tls", False):
            return None
        from ..net.tls import client_context
        ctx = client_context(
            ca_file=self.settings.tls_ca_file,
            cert_file=self.settings.tls_cert_file,
            key_file=self.settings.tls_key_file,
            verify=self.settings.tls_verify,
        )
        return (ctx, node.host)

    def broadcast_destinations_changed(self) -> None:
        self.ae_status.configure(text=f"My AE: {self.settings.my_aetitle}")
        for panel in self.panels.values():
            if hasattr(panel, "on_destinations_changed"):
                panel.on_destinations_changed()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
