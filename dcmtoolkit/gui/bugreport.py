"""'Report a Bug' dialog: collect a description, bundle scrubbed diagnostics,
open a pre-addressed email, and reveal the attachment for the reporter."""

from __future__ import annotations

import os
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
        self.geometry("560x520")
        self.transient(app)
        self.grab_set()

        ctk.CTkLabel(self, text="Report a Bug",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=PAD, pady=(PAD, 2))
        ctk.CTkLabel(
            self, justify="left", wraplength=520, text=(
                "This creates a small diagnostics file (app version, OS, and a "
                "PHI-scrubbed activity log - no images) and opens an email so "
                "you can send it. Review before sending."),
        ).pack(anchor="w", padx=PAD)

        ctk.CTkLabel(self, text="What happened?", anchor="w").pack(
            anchor="w", padx=PAD, pady=(PAD, 0))
        self.desc = ctk.CTkTextbox(self, height=120, wrap="word")
        self.desc.pack(fill="x", padx=PAD)

        ctk.CTkLabel(self, text="Steps to reproduce", anchor="w").pack(
            anchor="w", padx=PAD, pady=(PAD, 0))
        self.steps = ctk.CTkTextbox(self, height=100, wrap="word")
        self.steps.pack(fill="x", padx=PAD)

        self.include_log = ctk.CTkCheckBox(
            self, text="Attach PHI-scrubbed activity log")
        self.include_log.select()
        self.include_log.pack(anchor="w", padx=PAD, pady=PAD)

        to = support.resolve_bug_email(self.app.settings)
        note = (f"Will email: {to}" if to else
                "No recipient set - add one in Settings > Bug report email. "
                "The file will still be created so you can send it manually.")
        ctk.CTkLabel(self, text=note, text_color=("gray40", "gray65"),
                     anchor="w", wraplength=520, justify="left").pack(
            anchor="w", padx=PAD)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=PAD, pady=PAD)
        ctk.CTkButton(btns, text="Create report & open email",
                      command=self._submit).pack(side="left")
        ctk.CTkButton(btns, text="Cancel", fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=PAD)

    def _submit(self) -> None:
        desc = self.desc.get("1.0", "end").strip()
        if not desc:
            messagebox.showwarning("Report a Bug",
                                   "Please describe what happened.",
                                   parent=self)
            return
        try:
            zpath = support.build_report(
                desc, self.steps.get("1.0", "end"),
                include_log=bool(self.include_log.get()),
                settings=self.app.settings)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Report a Bug",
                                 f"Could not build the report:\n{exc}",
                                 parent=self)
            return

        to = support.resolve_bug_email(self.app.settings)
        url = support.mailto_url(to, support.default_subject(),
                                 support.report_body(zpath, self.app.settings))
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        # Reveal the file so the reporter can attach it to the email.
        try:
            os.startfile(zpath.parent)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

        messagebox.showinfo(
            "Report created",
            "Your email should have opened.\n\n"
            f"Attach this file before sending:\n{zpath}\n\n"
            "(The folder has been opened for you.)",
            parent=self)
        self.destroy()
