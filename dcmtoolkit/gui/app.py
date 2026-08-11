"""Main application window: sidebar navigation + swappable tool panels."""

from __future__ import annotations

import customtkinter as ctk

from .. import APP_NAME, __version__, config, paths
from ..logging_setup import setup as setup_logging
from .panels_network import EchoPanel, SendPanel, QueryMovePanel
from .panels_files import TagListPanel, ModifyPanel, SplitPanel, DumpPanel
from .panels_settings import SettingsPanel

# Store receiver panel is optional (imports pywin32 lazily for service bits).
try:
    from .panels_receiver import ReceiverPanel
    _HAS_RECEIVER = True
except Exception:  # noqa: BLE001
    _HAS_RECEIVER = False


NAV_GROUPS = [
    ("Network", [EchoPanel, SendPanel, QueryMovePanel]),
    ("Files", [TagListPanel, ModifyPanel, SplitPanel, DumpPanel]),
]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        setup_logging()
        self.settings = config.load_settings()
        self.destinations = config.load_destinations()

        ctk.set_appearance_mode(self.settings.appearance)
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("980x680")
        self.minsize(820, 560)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar --------------------------------------------------------
        self.sidebar = ctk.CTkScrollableFrame(self, width=200,
                                              corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="DICOM\nToolkit",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     justify="left").pack(anchor="w", padx=12, pady=(12, 8))

        # Content --------------------------------------------------------
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.panels: dict[str, ctk.CTkFrame] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._build_nav()

        # Status bar -----------------------------------------------------
        status = ctk.CTkFrame(self, height=24, corner_radius=0)
        status.grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(status, text=f"Data: {paths.data_dir()}",
                     text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=8)
        self.ae_status = ctk.CTkLabel(
            status, text=f"My AE: {self.settings.my_aetitle}",
            text_color="gray", font=ctk.CTkFont(size=11))
        self.ae_status.pack(side="right", padx=8)

        # Show first panel
        if self._buttons:
            self.select(next(iter(self.panels)))

    def _build_nav(self) -> None:
        groups = list(NAV_GROUPS)
        receiver_group = []
        if _HAS_RECEIVER:
            receiver_group = [("Receiver", [ReceiverPanel])]
        groups = groups + receiver_group + [("Config", [SettingsPanel])]

        for group_name, panel_classes in groups:
            ctk.CTkLabel(self.sidebar, text=group_name.upper(),
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="gray").pack(anchor="w", padx=12,
                                                 pady=(10, 2))
            for cls in panel_classes:
                key = cls.__name__
                panel = cls(self.content, self)
                panel.grid(row=0, column=0, sticky="nsew")
                panel.grid_remove()
                self.panels[key] = panel

                btn = ctk.CTkButton(
                    self.sidebar, text=cls.title, anchor="w",
                    fg_color="transparent", text_color=("gray10", "gray90"),
                    hover_color=("gray75", "gray25"),
                    command=lambda k=key: self.select(k))
                btn.pack(fill="x", padx=8, pady=1)
                self._buttons[key] = btn

    def select(self, key: str) -> None:
        for k, panel in self.panels.items():
            if k == key:
                panel.grid()
                if hasattr(panel, "on_show"):
                    panel.on_show()
            else:
                panel.grid_remove()
        for k, btn in self._buttons.items():
            btn.configure(fg_color=("gray80", "gray28") if k == key
                          else "transparent")

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
