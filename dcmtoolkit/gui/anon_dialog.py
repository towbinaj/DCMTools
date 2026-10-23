"""Manage anonymization profiles: pick/create/delete a named profile and edit
its per-tag rules (Remove / Blank / Replace / Keep) in a table."""

from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from .. import anon_profiles as ap
from .widgets import PAD


def _fmt_tag(hex8: str) -> str:
    """'00100010' -> '0010,0010' for display."""
    h = (hex8 or "").strip().replace(",", "")
    return f"{h[:4]},{h[4:8]}" if len(h) >= 8 else hex8


class _RuleRow:
    def __init__(self, master, rule: ap.AnonRule, on_delete):
        self.frame = ctk.CTkFrame(master, fg_color="transparent")
        self.frame.grid_columnconfigure(1, weight=1)
        self.frame.grid_columnconfigure(3, weight=1)
        self.tag = ctk.CTkEntry(self.frame, width=90,
                                placeholder_text="gggg,eeee")
        self.tag.insert(0, _fmt_tag(rule.tag))
        self.tag.grid(row=0, column=0, padx=2, pady=2)
        self.kw = ctk.CTkLabel(self.frame, text="", width=180, anchor="w",
                               text_color=("gray40", "gray65"))
        self.kw.grid(row=0, column=1, padx=2, sticky="w")
        self.action = ctk.CTkOptionMenu(self.frame, values=ap.ACTIONS, width=110,
                                        command=lambda _v: self._sync())
        self.action.set(rule.action if rule.action in ap.ACTIONS else "REMOVE")
        self.action.grid(row=0, column=2, padx=2)
        self.value = ctk.CTkEntry(self.frame, placeholder_text="replacement value")
        self.value.insert(0, rule.value)
        self.value.grid(row=0, column=3, padx=2, sticky="ew")
        ctk.CTkButton(self.frame, text="X", width=28, fg_color="#a33",
                      hover_color="#c44",
                      command=lambda: on_delete(self)).grid(row=0, column=4, padx=2)
        self.tag.bind("<KeyRelease>", lambda _e: self._sync())
        self._sync()

    def _sync(self) -> None:
        self.kw.configure(text=ap.keyword_for(self.tag.get()) or "(unknown tag)")
        is_replace = self.action.get() == "REPLACE"
        self.value.configure(state="normal" if is_replace else "disabled")

    def to_rule(self) -> ap.AnonRule | None:
        tag = self.tag.get().strip().replace(",", "").upper()
        if len(tag) != 8:
            return None
        return ap.AnonRule(tag=tag, action=self.action.get(),
                           value=self.value.get())

    def destroy(self) -> None:
        self.frame.destroy()


class AnonProfileDialog(ctk.CTkToplevel):
    def __init__(self, app, on_saved=None, select: str = ""):
        super().__init__(app)
        self.app = app
        self.on_saved = on_saved
        self.title("Anonymization Profiles")
        self.geometry("900x640")
        self.minsize(680, 480)
        self.transient(app)
        self.grab_set()

        self._profiles = ap.load_profiles()
        self._rows: list[_RuleRow] = []

        # Top: profile selector + profile-level actions.
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=PAD, pady=PAD)
        ctk.CTkLabel(top, text="Profile").pack(side="left", padx=(PAD, 4))
        self.sel = ctk.CTkOptionMenu(top, values=self._names(), width=240,
                                     command=self._on_select)
        self.sel.pack(side="left")
        ctk.CTkButton(top, text="New", width=60, command=self._new).pack(
            side="left", padx=4)
        ctk.CTkButton(top, text="Duplicate", width=90,
                      command=self._duplicate).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Delete", width=70, fg_color="#a33",
                      hover_color="#c44", command=self._delete).pack(
            side="left", padx=4)

        # Name (its own row so the flags don't crowd it off-screen).
        meta = ctk.CTkFrame(self)
        meta.pack(fill="x", padx=PAD)
        meta.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(meta, text="Name").grid(row=0, column=0, padx=(PAD, 4),
                                             pady=PAD, sticky="w")
        self.name = ctk.CTkEntry(meta)
        self.name.grid(row=0, column=1, padx=4, pady=PAD, sticky="ew")
        # Profile-level flags on a second row.
        flags = ctk.CTkFrame(meta, fg_color="transparent")
        flags.grid(row=1, column=0, columnspan=2, padx=PAD, pady=(0, PAD),
                   sticky="w")
        self.basic = ctk.CTkCheckBox(flags, text="PS3.15 Basic Profile")
        self.basic.pack(side="left")
        ctk.CTkButton(flags, text="What's in Basic?", width=120,
                      command=self._explain_basic).pack(side="left", padx=(6, PAD))
        self.retain = ctk.CTkCheckBox(flags, text="Retain study dates")
        self.retain.pack(side="left", padx=PAD)
        self.rmpriv = ctk.CTkCheckBox(flags, text="Remove private")
        self.rmpriv.pack(side="left", padx=PAD)

        # Help: what unlisted tags do, and the tag format (two short lines so
        # nothing clips).
        for line in (
            "With PS3.15 Basic on, the standard identifiers are removed "
            "automatically - add rules only for EXTRA tags, or Keep to exempt "
            "one.",
            "A tag not listed and not covered by Basic is left UNCHANGED.  "
            "Enter tags as gggg,eeee (hex; no 'x' for standard tags).",
        ):
            ctk.CTkLabel(self, text=line, anchor="w", justify="left",
                         text_color=("gray35", "gray70"),
                         font=ctk.CTkFont(size=11)).pack(
                fill="x", padx=PAD, anchor="w")

        # Rule table header.
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 0))
        ctk.CTkLabel(hdr, text="Per-tag rules  (Keep = do NOT anonymize)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ Add tag", width=90,
                      command=lambda: self._add_rule()).pack(side="right")
        self.common = ctk.CTkOptionMenu(
            hdr, width=220, dynamic_resizing=False,
            values=["Common tags ▾"] + [
                f"{kw} ({_fmt_tag(tag)})" for tag, kw in ap.COMMON_TAGS],
            command=self._add_common)
        self.common.set("Common tags ▾")
        self.common.pack(side="right", padx=6)

        # Bottom: save / close - pinned before the expanding table so they stay
        # visible even when the window is short.
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=PAD, pady=PAD)
        ctk.CTkButton(bottom, text="Save profiles", command=self._save).pack(
            side="left")
        ctk.CTkButton(bottom, text="Close", fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=PAD)

        self.rows_frame = ctk.CTkScrollableFrame(self, height=240)
        self.rows_frame.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.rows_frame.grid_columnconfigure(0, weight=1)

        start = select if select in self._names() else self._names()[0]
        self.sel.set(start)
        self._load_into_editor(start)

    def _explain_basic(self) -> None:
        messagebox.showinfo("PS3.15 Basic Confidentiality Profile",
                            ap.BASIC_PROFILE_EXPLANATION, parent=self)

    # -- helpers ---------------------------------------------------------
    def _names(self) -> list[str]:
        return [p.name for p in self._profiles] or ["(none)"]

    def _current(self) -> ap.AnonProfile | None:
        name = self.sel.get()
        for p in self._profiles:
            if p.name == name:
                return p
        return None

    def _commit_editor(self) -> None:
        """Write the editor's widgets back into the selected profile object."""
        prof = self._current()
        if prof is None:
            return
        prof.name = self.name.get().strip() or prof.name
        prof.basic_profile = bool(self.basic.get())
        prof.retain_dates = bool(self.retain.get())
        prof.remove_private = bool(self.rmpriv.get())
        rules = [r.to_rule() for r in self._rows]
        prof.rules = [r for r in rules if r is not None]

    def _load_into_editor(self, name: str) -> None:
        prof = next((p for p in self._profiles if p.name == name), None)
        for r in self._rows:
            r.destroy()
        self._rows = []
        if prof is None:
            return
        self.name.delete(0, "end")
        self.name.insert(0, prof.name)
        (self.basic.select if prof.basic_profile else self.basic.deselect)()
        (self.retain.select if prof.retain_dates else self.retain.deselect)()
        (self.rmpriv.select if prof.remove_private else self.rmpriv.deselect)()
        for rule in prof.rules:
            self._add_rule(rule)

    def _add_rule(self, rule: ap.AnonRule | None = None) -> None:
        row = _RuleRow(self.rows_frame, rule or ap.AnonRule("", "REMOVE"),
                       on_delete=self._del_rule)
        row.frame.grid(sticky="ew", pady=1)
        self._rows.append(row)

    def _del_rule(self, row: _RuleRow) -> None:
        row.destroy()
        self._rows.remove(row)

    def _add_common(self, choice: str) -> None:
        self.common.set("Common tags ▾")
        if "(" in choice and choice.endswith(")"):
            tag = choice[choice.rindex("(") + 1:-1]
            self._add_rule(ap.AnonRule(tag, "REMOVE"))

    # -- profile actions -------------------------------------------------
    def _on_select(self, name: str) -> None:
        # Save edits of the previously shown profile into memory first.
        # (sel already changed; commit using the new widgets would be wrong, so
        # we commit on explicit actions/save instead.)
        self._load_into_editor(name)

    def _unique_name(self, base: str) -> str:
        names = set(self._names())
        if base not in names:
            return base
        i = 2
        while f"{base} {i}" in names:
            i += 1
        return f"{base} {i}"

    def _new(self) -> None:
        self._commit_editor()
        prof = ap.AnonProfile(self._unique_name("New profile"))
        self._profiles.append(prof)
        self.sel.configure(values=self._names())
        self.sel.set(prof.name)
        self._load_into_editor(prof.name)

    def _duplicate(self) -> None:
        self._commit_editor()
        cur = self._current()
        if cur is None:
            return
        copy = ap.AnonProfile.from_dict(cur.to_dict())
        copy.name = self._unique_name(cur.name + " copy")
        self._profiles.append(copy)
        self.sel.configure(values=self._names())
        self.sel.set(copy.name)
        self._load_into_editor(copy.name)

    def _delete(self) -> None:
        cur = self._current()
        if cur is None:
            return
        if len(self._profiles) == 1:
            messagebox.showinfo("Profiles", "Keep at least one profile.",
                                parent=self)
            return
        if not messagebox.askyesno("Delete profile",
                                   f"Delete '{cur.name}'?", parent=self):
            return
        self._profiles.remove(cur)
        self.sel.configure(values=self._names())
        self.sel.set(self._names()[0])
        self._load_into_editor(self.sel.get())

    def _save(self) -> None:
        self._commit_editor()
        names = [p.name for p in self._profiles]
        if len(set(names)) != len(names):
            messagebox.showerror("Profiles",
                                 "Profile names must be unique.", parent=self)
            return
        ap.save_profiles(self._profiles)
        if self.on_saved:
            self.on_saved(self.name.get().strip())
        messagebox.showinfo("Profiles", "Saved.", parent=self)
