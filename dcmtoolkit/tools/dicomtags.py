"""DICOM tag resolution and VR value validation helpers.

Used by the Modify Header tool (tag name lookup + common-tag picker) and the
patient/study demographic editors (VR validation).
"""

from __future__ import annotations

import re

from pydicom import config as _pdconfig
from pydicom.datadict import keyword_for_tag, tag_for_keyword
from pydicom.tag import Tag
from pydicom.valuerep import validate_value

# Friendly label -> DICOM keyword, for the common-tag dropdown.
COMMON_TAGS = [
    ("Patient Name", "PatientName"),
    ("Patient ID", "PatientID"),
    ("Patient Birth Date", "PatientBirthDate"),
    ("Patient Sex", "PatientSex"),
    ("Accession Number", "AccessionNumber"),
    ("Study Date", "StudyDate"),
    ("Study Time", "StudyTime"),
    ("Study Description", "StudyDescription"),
    ("Series Description", "SeriesDescription"),
    ("Study ID", "StudyID"),
    ("Referring Physician", "ReferringPhysicianName"),
    ("Institution Name", "InstitutionName"),
    ("Modality", "Modality"),
    ("Study Instance UID", "StudyInstanceUID"),
    ("Series Instance UID", "SeriesInstanceUID"),
]


def to_hex(tag: Tag) -> str:
    return f"{tag.group:04X}{tag.element:04X}"


def common_tag_labels() -> list[str]:
    out = []
    for label, kw in COMMON_TAGS:
        tg = tag_for_keyword(kw)
        if tg:
            t = Tag(tg)
            out.append(f"{label}  ({t.group:04X},{t.element:04X})")
    return out


def resolve_tag(text: str):
    """Resolve free text to (Tag, keyword). Accepts a friendly label with a
    "(gggg,eeee)" suffix, a raw ggggeeee / gggg,eeee, or a DICOM keyword.
    Returns (None, "") if it can't be resolved."""
    text = (text or "").strip()
    if not text:
        return None, ""
    m = re.search(r"([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{4})", text)
    if m:
        t = Tag(int(m.group(1), 16), int(m.group(2), 16))
        return t, keyword_for_tag(t) or ""
    compact = text.replace(" ", "")
    if re.fullmatch(r"[0-9A-Fa-f]{8}", compact):
        t = Tag(int(compact[:4], 16), int(compact[4:], 16))
        return t, keyword_for_tag(t) or ""
    tg = tag_for_keyword(compact)
    if tg:
        t = Tag(tg)
        return t, keyword_for_tag(t) or text
    return None, ""


def validate(vr: str, value: str) -> tuple[bool, str]:
    """Validate a string value against a DICOM VR. Empty is always allowed
    (Type 2). Returns (ok, message)."""
    if value is None or value == "":
        return True, ""
    try:
        validate_value(vr, value, _pdconfig.RAISE)
    except Exception as exc:  # noqa: BLE001 - message is the useful part
        return False, str(exc).split(" Please see")[0].strip()
    # pydicom checks DA/DT format but not calendar validity (e.g. Feb 30).
    if vr in ("DA", "DT") and len(value) >= 8 and value[:8].isdigit():
        from datetime import date
        try:
            date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return False, f"'{value[:8]}' is not a real calendar date "
    return True, ""
