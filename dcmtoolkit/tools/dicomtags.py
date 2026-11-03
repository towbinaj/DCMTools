"""DICOM tag resolution and VR value validation helpers.

Used by the Modify Header tool (tag name lookup + common-tag picker) and the
patient/study demographic editors (VR validation).
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydicom import config as _pdconfig
from pydicom.datadict import DicomDictionary, keyword_for_tag, tag_for_keyword
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


# Pixel Data - never a header-edit target, so keep it out of the picker.
_PIXEL_DATA = Tag(0x7FE0, 0x0010)


def dataset_tag_labels(ds) -> list[str]:
    """Picker labels ("Keyword  (gggg,eeee)") for every top-level element of a
    dataset, in tag order. Lets the Modify tool offer the tags actually present
    in the loaded study instead of a fixed common-tag list. Skips group-length
    (gggg,0000) and Pixel Data; unknown/private tags show their name or
    'Private'. Each label resolves back via :func:`resolve_tag`."""
    out = []
    for elem in ds:
        t = elem.tag
        if t.element == 0x0000 or t == _PIXEL_DATA:
            continue
        name = keyword_for_tag(t)
        if not name:
            n = (getattr(elem, "name", "") or "").strip()
            name = n if n and not n.startswith("[") else "Private"
        out.append(f"{name}  ({t.group:04X},{t.element:04X})")
    return out


def file_tag_labels(path) -> list[str]:
    """:func:`dataset_tag_labels` for the header of a DICOM file on disk
    (pixel data is not read). Returns [] if the file can't be read."""
    try:
        from pydicom import dcmread
        ds = dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:  # noqa: BLE001 - a bad/non-DICOM file just yields nothing
        return []
    return dataset_tag_labels(ds)


@lru_cache(maxsize=1)
def _keyword_index() -> dict[str, int]:
    """Lowercased DICOM keyword -> tag int, built once, so a typed name
    resolves regardless of case (e.g. "patientname" -> PatientName)."""
    idx: dict[str, int] = {}
    for tag_int, entry in DicomDictionary.items():
        kw = entry[4]
        if kw:
            idx.setdefault(kw.lower(), tag_int)
    return idx


def resolve_tag(text: str):
    """Resolve free text to (Tag, keyword). Accepts a friendly label with a
    "(gggg,eeee)" suffix, a raw ggggeeee / gggg,eeee, or a DICOM keyword.
    Keyword matching ignores case and spaces ("patient name" -> PatientName).
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
    tg = tag_for_keyword(compact) or _keyword_index().get(compact.lower())
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
