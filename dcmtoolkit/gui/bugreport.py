"""'Report a Bug' dialog: one field. Builds a self-contained email (the
description plus a PHI-scrubbed diagnostics/log tail inlined, so there is
nothing to attach) and opens the reporter's mail client."""

from __future__ import annotations

import webbrowser
from tkinter import messagebox

import customtkinter as ctk

from .. import support
from .widgets import PAD


class BugReportDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Report a Bug")
        self.geometry("560x420")
        self.transient(app)
        self.grab_set()

        ctk.CTkLabel(self, text="Report a Bug",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=PAD, pady=(PAD, 2))

        to = support.resolve_bug_email(self.app.settings)
        intro = ("Describe the problem (and how to reproduce it). "
                 "App version, OS and a PHI-scrubbed activity-log tail are "
                 "added automatically - no images, nothing to attach.")
        ctk.CTkLabel(self, justify="left", wraplength=520, text=intro).pack(
            anchor="w", padx=PAD)

        self.desc = ctk.CTkTextbox(self, height=210, wrap="word")
        self.desc.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.desc.focus_set()

        note = (f"Opens an email to {to}" if to else
                "No recipient set - add one in Settings > Bug report email. "
                "Your email will still open so you can choose a recipient.")
        ctk.CTkLabel(self, text=note, text_color=("gray40", "gray65"),
                     anchor="w", wraplength=520, justify="left").pack(
            anchor="w", padx=PAD)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=PAD, pady=PAD)
        ctk.CTkButton(btns, text="Open email", command=self._submit).pack(
            side="left")
        ctk.CTkButton(btns, text="Cancel", fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=PAD)

    def _submit(self) -> None:
        desc = self.desc.get("1.0", "end").strip()
        if not desc:
            messagebox.showwarning("Report a Bug",
                                   "Please describe the problem first.",
                                   parent=self)
            return
        to = support.resolve_bug_email(self.app.settings)
        body = support.inline_body(desc, self.app.settings)
        url = support.mailto_url(to, support.default_subject(), body)
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Report a Bug",
                                 f"Could not open your email client:\n{exc}",
                                 parent=self)
            return
        self.destroy()
