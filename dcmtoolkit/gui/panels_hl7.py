"""HL7 Sender: send HL7 v2 messages (ADT / ORU / ORM) over MLLP, read the ACK.

Send-only, mimics SmartHL7.Sender. Paste/edit a single message, or load a batch
of .hl7 files. Light per-type awareness: the mode selects a starter template and
checks that the type's expected segments are present before sending.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config
from ..hl7 import mllp
from ..hl7 import message as hmsg
from .base import ToolPanel
from .theme import MUTED
from .widgets import run_threaded, PAD

# Synthetic starter templates (no real patient/site data). Field separator '|',
# encoding chars '^~\&'. Users replace the placeholder values.
_TEMPLATES = {
    "ADT": ("MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|000000000000|1|ADT^A01|"
            "MSGID|T|2.5\r"
            "EVN|A01|000000000000\r"
            "PID|1||PATID^^^^MR||LAST^FIRST||YYYYMMDD|M\r"
            "PV1|1|I|WARD^ROOM^BED\r"),
    "ORU": ("MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|000000000000|1|ORU^R01|"
            "MSGID|T|2.5\r"
            "PID|1||PATID^^^^MR||LAST^FIRST||YYYYMMDD|M\r"
            "OBR|1|PLACERID|FILLERID|CODE^Description\r"
            "OBX|1|TX|CODE^Description||Result text\r"),
    "ORM": ("MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|000000000000|1|ORM^O01|"
            "MSGID|T|2.5\r"
            "PID|1||PATID^^^^MR||LAST^FIRST||YYYYMMDD|M\r"
            "ORC|NW|PLACERID\r"
            "OBR|1|PLACERID|FILLERID|CODE^Description\r"),
}
_MODES = ["Auto", "ADT", "ORU", "ORM"]


class HL7SenderPanel(ToolPanel):
    title = "HL7 Sender"
    description = ("Send HL7 v2 (ADT / ORU / ORM) over MLLP and read the ACK. "
                  "Paste one message or load a batch of .hl7 files.")

    def build(self) -> None:
        self.files: list[Path] = []
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)
        self.log.configure(height=120)

        # Endpoint row -------------------------------------------------
        ep = ctk.CTkFrame(self.body)
        ep.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkLabel(ep, text="Saved").grid(row=0, column=0, padx=(PAD, 4),
                                            pady=PAD)
        self.saved = ctk.CTkOptionMenu(ep, width=180, values=["(none)"],
                                       command=self._on_saved)
        self.saved.grid(row=0, column=1, pady=PAD)
        ctk.CTkLabel(ep, text="Host").grid(row=0, column=2, padx=(PAD, 4))
        self.host = ctk.CTkEntry(ep, width=180)
        self.host.grid(row=0, column=3)
        ctk.CTkLabel(ep, text="Port").grid(row=0, column=4, padx=(PAD, 4))
        self.port = ctk.CTkEntry(ep, width=70)
        self.port.insert(0, "2575")      # common HL7/MLLP port
        self.port.grid(row=0, column=5)
        ctk.CTkButton(ep, text="Save endpoint", width=110,
                      command=self._save_ep).grid(row=0, column=6, padx=PAD)
        ctk.CTkButton(ep, text="Delete", width=70, fg_color="#a33",
                      hover_color="#c44", command=self._del_ep).grid(
            row=0, column=7, padx=(0, PAD))

        # Mode / options row ------------------------------------------
        opt = ctk.CTkFrame(self.body, fg_color="transparent")
        opt.grid(row=1, column=0, sticky="ew", padx=PAD)
        ctk.CTkLabel(opt, text="Mode").pack(side="left", padx=(0, 4))
        self.mode = ctk.CTkOptionMenu(opt, values=_MODES, width=90,
                                      command=self._on_mode)
        self.mode.pack(side="left")
        ctk.CTkButton(opt, text="Insert template", width=120,
                      command=self._insert_template).pack(side="left", padx=PAD)
        self.update_msh = ctk.CTkCheckBox(
            opt, text="Update MSH timestamp + control ID on send")
        self.update_msh.select()
        self.update_msh.pack(side="left", padx=PAD)
        ctk.CTkButton(opt, text="Load .hl7 file(s)...", width=140,
                      command=self._add_files).pack(side="right", padx=4)
        ctk.CTkButton(opt, text="Load folder...", width=120,
                      command=self._add_folder).pack(side="right", padx=4)
        self.count_lbl = ctk.CTkLabel(opt, text="", text_color=MUTED)
        self.count_lbl.pack(side="right", padx=PAD)

        # Message editor ----------------------------------------------
        self.msg = ctk.CTkTextbox(self.body, wrap="none",
                                  font=ctk.CTkFont(family="Consolas", size=13))
        self.msg.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=PAD)

        # Actions ------------------------------------------------------
        act = ctk.CTkFrame(self.body, fg_color="transparent")
        act.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.send_btn = ctk.CTkButton(act, text="Send", command=self._send)
        self.send_btn.pack(side="left")
        ctk.CTkButton(act, text="Clear", width=70, fg_color="gray40",
                      command=self._clear).pack(side="left", padx=PAD)
        self.type_lbl = ctk.CTkLabel(act, text="", text_color=MUTED)
        self.type_lbl.pack(side="left", padx=PAD)
        self.msg.bind("<KeyRelease>", lambda _e: self._refresh_type())

        self._refresh_saved()

    # -- endpoints -------------------------------------------------------
    def _endpoints(self) -> list:
        return list(self.app.settings.hl7_endpoints or [])

    def _refresh_saved(self) -> None:
        names = [e.get("name", "") for e in self._endpoints() if e.get("name")]
        self.saved.configure(values=names or ["(none)"])
        self.saved.set(names[0] if names else "(none)")

    def _on_saved(self, name: str) -> None:
        for e in self._endpoints():
            if e.get("name") == name:
                self.host.delete(0, "end"); self.host.insert(0, e.get("host", ""))
                self.port.delete(0, "end"); self.port.insert(0, str(e.get("port", "")))
                return

    def _save_ep(self) -> None:
        host = self.host.get().strip()
        port = self.port.get().strip()
        if not host or not port.isdigit():
            messagebox.showwarning("HL7", "Enter a host and numeric port first.",
                                   parent=self)
            return
        name = f"{host}:{port}"
        eps = [e for e in self._endpoints() if e.get("name") != name]
        eps.append({"name": name, "host": host, "port": int(port)})
        eps.sort(key=lambda e: e.get("name", "").lower())
        self.app.settings.hl7_endpoints = eps
        config.save_settings(self.app.settings)
        self._refresh_saved()
        self.saved.set(name)

    def _del_ep(self) -> None:
        name = self.saved.get()
        eps = [e for e in self._endpoints() if e.get("name") != name]
        self.app.settings.hl7_endpoints = eps
        config.save_settings(self.app.settings)
        self._refresh_saved()

    # -- mode / message --------------------------------------------------
    def _on_mode(self, _mode: str) -> None:
        if not self.msg.get("1.0", "end").strip():
            self._insert_template()
        self._refresh_type()

    def _insert_template(self) -> None:
        mode = self.mode.get()
        if mode == "Auto":
            messagebox.showinfo("HL7", "Pick a mode (ADT/ORU/ORM) to insert a "
                                "template.", parent=self)
            return
        tpl = _TEMPLATES.get(mode, "")
        self.msg.delete("1.0", "end")
        self.msg.insert("1.0", tpl.replace("\r", "\n"))
        self._refresh_type()

    def _refresh_type(self) -> None:
        text = self.msg.get("1.0", "end")
        mt = hmsg.message_type(text)
        miss = hmsg.missing_required(text)
        parts = []
        if mt:
            parts.append(f"Type: {mt}")
        if miss:
            parts.append(f"missing segment(s): {', '.join(miss)}")
        self.type_lbl.configure(
            text="  -  ".join(parts),
            text_color=("#d9695f" if miss else MUTED))

    # -- batch loading ---------------------------------------------------
    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            filetypes=[("HL7", "*.hl7 *.txt"), ("All files", "*.*")])
        self._ingest(chosen)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self._ingest(Path(folder).rglob("*.hl7"))

    def _ingest(self, paths) -> None:
        self.files = [Path(p) for p in paths if Path(p).is_file()]
        self.count_lbl.configure(
            text=f"{len(self.files)} file(s) queued" if self.files else "")

    def _clear(self) -> None:
        self.msg.delete("1.0", "end")
        self.files = []
        self.count_lbl.configure(text="")
        self.type_lbl.configure(text="")
        self.log.clear()

    # -- send ------------------------------------------------------------
    def _send(self) -> None:
        host = self.host.get().strip()
        port = self.port.get().strip()
        if not host or not port.isdigit():
            self.log.write("Enter a host and numeric port.")
            return
        update = bool(self.update_msh.get())
        timeout = 10.0

        # Build the list of (label, message) to send.
        items: list[tuple[str, str]] = []
        if self.files:
            for f in self.files:
                try:
                    items.append((f.name, f.read_text(encoding="utf-8",
                                                       errors="replace")))
                except OSError as exc:
                    self.log.write(f"[SKIP] {f.name}: {exc}")
        else:
            text = self.msg.get("1.0", "end")
            if not text.strip():
                self.log.write("Nothing to send - paste a message or load files.")
                return
            items.append(("message", text))

        self.send_btn.configure(state="disabled")
        self.log.write(f"--- Sending {len(items)} message(s) to {host}:{port} ---")

        def work():
            results = []
            for label, msg in items:
                out = hmsg.with_updated_msh(msg) if update else msg
                res = mllp.send_message(host, int(port), out, timeout=timeout)
                results.append((label, hmsg.message_type(out), res))
            return results

        def done(results):
            ok = 0
            for label, mtype, res in results:
                if res.error:
                    self.log.write(f"[ERROR] {label} ({mtype}): {res.error}")
                elif res.ok:
                    ok += 1
                    self.log.write(f"[AA] {label} ({mtype}) accepted"
                                   + (f" - {res.text}" if res.text else ""))
                else:
                    self.log.write(f"[{res.code or 'NOACK'}] {label} ({mtype}) "
                                   f"NOT accepted"
                                   + (f" - {res.text}" if res.text else ""))
            self.log.write(f"[DONE] {ok}/{len(results)} accepted (AA).")
            self.send_btn.configure(state="normal")

        def err(exc, tb):
            self.log.write(f"[ERROR] {exc}")
            self.send_btn.configure(state="normal")

        run_threaded(self, work, done, err)
