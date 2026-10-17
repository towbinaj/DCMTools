"""Named anonymization profiles with custom per-tag rules.

A profile bundles the standards-based toggles (PS3.15 Basic Profile, retain
study dates, remove private tags) with a list of explicit per-tag rules:

* REMOVE  - delete the tag
* BLANK   - keep the tag but empty its value
* REPLACE - set the tag to a fixed value
* KEEP    - exempt the tag from the Basic Profile (do NOT anonymize it)

Profiles persist to ``anon_profiles.json`` in the (git-ignored) data folder,
so they never reach the repo. A set of built-in profiles is always available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from pydicom.datadict import keyword_for_tag

from . import paths

ACTIONS = ["REMOVE", "BLANK", "REPLACE", "KEEP"]

# Curated tags for the quick-add menu: (hex tag, keyword).
COMMON_TAGS = [
    ("00100010", "PatientName"),
    ("00100020", "PatientID"),
    ("00100030", "PatientBirthDate"),
    ("00100040", "PatientSex"),
    ("00101010", "PatientAge"),
    ("00080050", "AccessionNumber"),
    ("00080090", "ReferringPhysicianName"),
    ("00081070", "OperatorsName"),
    ("00080080", "InstitutionName"),
    ("00081040", "InstitutionalDepartmentName"),
    ("00081030", "StudyDescription"),
    ("0008103E", "SeriesDescription"),
    ("00200010", "StudyID"),
    ("00101000", "OtherPatientIDs"),
    ("00102160", "EthnicGroup"),
    ("00104000", "PatientComments"),
]


def keyword_for(hex_tag: str) -> str:
    """Human keyword for an 8-hex tag like '00100010', or '' if unknown."""
    try:
        s = hex_tag.strip().replace(",", "").replace("x", "")
        kw = keyword_for_tag((int(s[:4], 16) << 16) | int(s[4:8], 16))
        return kw or ""
    except (ValueError, IndexError):
        return ""


@dataclass
class AnonRule:
    tag: str            # 8 hex digits, e.g. "00100010"
    action: str = "REMOVE"
    value: str = ""     # used only by REPLACE

    def normalized(self) -> "AnonRule":
        return AnonRule(tag=self.tag.strip().replace(",", "").upper(),
                        action=(self.action or "REMOVE").upper(),
                        value=self.value)


@dataclass
class AnonProfile:
    name: str
    basic_profile: bool = True
    retain_dates: bool = False
    remove_private: bool = True
    rules: list = field(default_factory=list)   # list[AnonRule]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rules"] = [asdict(r) for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnonProfile":
        rules = [AnonRule(**{k: r.get(k, "") for k in ("tag", "action", "value")})
                 for r in d.get("rules", [])]
        return cls(name=d.get("name", "Unnamed"),
                   basic_profile=bool(d.get("basic_profile", True)),
                   retain_dates=bool(d.get("retain_dates", False)),
                   remove_private=bool(d.get("remove_private", True)),
                   rules=rules)

    def custom_rules(self) -> list[tuple[str, str, str]]:
        """(tag, action, value) tuples for ReceiverConfig.custom_rules."""
        out = []
        for r in self.rules:
            rn = r.normalized()
            if rn.tag and rn.action in ACTIONS:
                out.append((rn.tag, rn.action, rn.value))
        return out


def default_profiles() -> list[AnonProfile]:
    return [
        AnonProfile("PS3.15 Basic (recommended)",
                    basic_profile=True, retain_dates=False, remove_private=True),
        AnonProfile("Research - keep study dates",
                    basic_profile=True, retain_dates=True, remove_private=True),
        AnonProfile("Basic but keep Patient ID",
                    basic_profile=True, remove_private=True,
                    rules=[AnonRule("00100020", "KEEP")]),
    ]


def _path():
    return paths.data_dir() / "anon_profiles.json"


def load_profiles() -> list[AnonProfile]:
    """Saved profiles, or the built-in defaults if none are saved yet."""
    p = _path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            profs = [AnonProfile.from_dict(d) for d in raw]
            if profs:
                return profs
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return default_profiles()


def save_profiles(profiles: list[AnonProfile]) -> None:
    _path().write_text(
        json.dumps([p.to_dict() for p in profiles], indent=2),
        encoding="utf-8")
