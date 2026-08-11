"""Settings panel: identity, appearance, and the destinations editor."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config
from ..importer import (import_legacy, import_any, export_csv, export_json)
from ..model import Node
from .base import ToolPanel
from .widgets import PAD


class _DestRow(ctk.CTkFrame):
    def __init__(self, master, node: Node, on_delete):
        super().__init__(master)
        self.grid_columnconfigure((0, 1, 2), weight=1)
        self.name = ctk.CTkEntry(self, placeholder_text="Name")
        self.name.insert(0, node.name)
        self.name.grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        self.aet = ctk.CTkEntry(self, placeholder_text="AE Title")
        self.aet.insert(0, node.aetitle)
        self.aet.grid(row=0, column=1, padx=2, pady=2, sticky="ew")
        self.host = ctk.CTkEntry(self, placeholder_text="Host")
        self.host.insert(0, node.host)
        self.host.grid(row=0, column=2, padx=2, pady=2, sticky="ew")
        self.port = ctk.CTkEntry(self, width=70, placeholder_text="Port")
        self.port.insert(0, str(node.port))
        self.port.grid(row=0, column=3, padx=2, pady=2)
        ctk.CTkButton(self, text="X", width=28, fg_color="#a33",
                      hover_color="#c44",
                      command=lambda: on_delete(self)).grid(
            row=0, column=4, padx=2)

    def to_node(self) -> Node | None:
        if not self.aet.get().strip() and not self.host.get().strip():
            return None
        try:
            port = int(self.port.get().strip() or "104")
        except ValueError:
            port = 104
        return Node(name=self.name.get().strip() or self.aet.get().strip(),
                    aetitle=self.aet.get().strip(),
                    host=self.host.get().strip(), port=port)


class SettingsPanel(ToolPanel):
    title = "Settings"
    description = "Local AE identity, appearance, and saved destinations."

    def build(self) -> None:
        # Hide the log box; settings doesn't need it.
        self.log.grid_remove()
        self.grid_rowconfigure(1, weight=1)

        # Identity + appearance
        top = ctk.CTkFrame(self.body)
        top.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkLabel(top, text="My AE Title").grid(row=0, column=0, padx=PAD,
                                                   pady=PAD, sticky="w")
        self.my_ae = ctk.CTkEntry(top, width=200)
        self.my_ae.insert(0, self.app.settings.my_aetitle)
        self.my_ae.grid(row=0, column=1, padx=PAD, pady=PAD, sticky="w")

        ctk.CTkLabel(top, text="Appearance").grid(row=0, column=2, padx=PAD)
        self.appearance = ctk.CTkOptionMenu(
            top, values=["system", "light", "dark"],
            command=self._change_appearance)
        self.appearance.set(self.app.settings.appearance)
        self.appearance.grid(row=0, column=3, padx=PAD)

        # Destinations editor
        editor = ctk.CTkFrame(self.body)
        editor.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        editor.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(editor, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(header, text="Destinations",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="+ Add", width=70,
                      command=self._add_blank).pack(side="right", padx=2)
        ctk.CTkButton(header, text="Import file...", width=110,
                      command=self._import_file).pack(side="right", padx=2)
        ctk.CTkButton(header, text="Import legacy folder...", width=150,
                      command=self._import_legacy).pack(side="right", padx=2)
        ctk.CTkButton(header, text="Export...", width=80,
                      command=self._export).pack(side="right", padx=2)

        self.rows_frame = ctk.CTkScrollableFrame(editor, height=320)
        self.rows_frame.grid(row=1, column=0, sticky="nsew", pady=PAD)
        self.rows_frame.grid_columnconfigure(0, weight=1)
        editor.grid_rowconfigure(1, weight=1)

        self._rows: list[_DestRow] = []
        self._load_rows()

        ctk.CTkButton(self.body, text="Save settings + destinations",
                      command=self._save).grid(row=2, column=0, sticky="w",
                                               padx=PAD, pady=PAD)

    def _load_rows(self) -> None:
        for r in self._rows:
            r.destroy()
        self._rows = []
        for node in self.app.destinations:
            self._append_row(node)

    def _append_row(self, node: Node) -> None:
        row = _DestRow(self.rows_frame, node, on_delete=self._delete_row)
        row.grid(sticky="ew", pady=1)
        self._rows.append(row)

    def _add_blank(self) -> None:
        self._append_row(Node(name="", aetitle="", host="", port=104))

    def _delete_row(self, row: _DestRow) -> None:
        row.destroy()
        self._rows.remove(row)

    def _change_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Import destinations (CSV or JSON)",
            filetypes=[("Destinations", "*.csv *.json"),
                       ("CSV", "*.csv"), ("JSON", "*.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            res = import_any(Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import failed", str(exc))
            return
        self._merge_result(res)

    def _import_legacy(self) -> None:
        folder = filedialog.askdirectory(
            title="Select folder containing DCMSend/QueryMove/DropMove CSVs")
        if not folder:
            return
        res = import_legacy(Path(folder))
        if not res.source_files:
            messagebox.showwarning("Import",
                                   "No legacy CSV files found in that folder.")
            return
        self._merge_result(res)

    def _merge_result(self, res) -> None:
        """Merge an ImportResult into the editor by DICOM identity."""
        existing = {(n.aetitle.upper(), n.host, n.port)
                    for n in self._collect_rows()}
        added = 0
        for n in res.nodes:
            if (n.aetitle.upper(), n.host, n.port) not in existing:
                self._append_row(n)
                existing.add((n.aetitle.upper(), n.host, n.port))
                added += 1
        msg = [f"Imported {added} new destination(s) from "
               f"{', '.join(res.source_files)}."]
        if res.conflicts:
            msg.append("\nConflicts (same name, different endpoint):")
            msg.extend("  - " + c for c in res.conflicts)
        if res.warnings:
            msg.append("\nWarnings:")
            msg.extend("  - " + w for w in res.warnings)
        msg.append("\nRemember to click 'Save' to persist.")
        messagebox.showinfo("Import complete", "\n".join(msg))

    def _export(self) -> None:
        nodes = self._collect_rows()
        if not nodes:
            messagebox.showinfo("Export", "No destinations to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export destinations",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
            initialfile="destinations.csv")
        if not path:
            return
        try:
            if Path(path).suffix.lower() == ".json":
                export_json(nodes, Path(path))
            else:
                export_csv(nodes, Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export",
                            f"Exported {len(nodes)} destination(s) to:\n{path}")

    def _collect_rows(self) -> list[Node]:
        nodes = []
        for r in self._rows:
            n = r.to_node()
            if n:
                nodes.append(n)
        return nodes

    def _save(self) -> None:
        self.app.settings.my_aetitle = self.my_ae.get().strip() or "DICOMTOOLKIT"
        self.app.settings.appearance = self.appearance.get()
        config.save_settings(self.app.settings)

        nodes = self._collect_rows()
        self.app.destinations = nodes
        config.save_destinations(nodes)
        self.app.broadcast_destinations_changed()
        messagebox.showinfo("Saved",
                            f"Saved settings and {len(nodes)} destination(s).")
