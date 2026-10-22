"""Light HL7 v2 message helpers: segments, MSH fields, message type, and
safe MSH timestamp / control-id rewriting. Deliberately minimal - no full
grammar, just what the sender needs."""

from __future__ import annotations

import random
from datetime import datetime

# Message-type families we support, and the segments each should contain.
REQUIRED_SEGMENTS = {
    "ADT": ["PID"],
    "ORU": ["PID", "OBR", "OBX"],
    "ORM": ["PID", "ORC", "OBR"],
}


def split_segments(message: str) -> list[str]:
    """Segments of a message, tolerant of \\r, \\n or \\r\\n separators."""
    text = message.replace("\r\n", "\r").replace("\n", "\r")
    return [s for s in text.split("\r") if s.strip()]


def normalize(message: str) -> str:
    """Canonical wire form: segments joined by a single CR, trailing CR."""
    segs = split_segments(message)
    return ("\r".join(segs) + "\r") if segs else ""


def field_sep(message: str) -> str:
    """The field separator (MSH-1) - the char right after 'MSH'. Default '|'."""
    for seg in split_segments(message):
        if seg.startswith("MSH") and len(seg) > 3:
            return seg[3]
    return "|"


def _msh(message: str) -> list[str] | None:
    for seg in split_segments(message):
        if seg.startswith("MSH"):
            return seg.split(field_sep(message))
    return None


def _get(fields: list[str], idx1: int) -> str:
    # HL7 is 1-based; for MSH, field 1 is the separator itself, so MSH-2 is
    # fields[1], MSH-9 is fields[8], etc.
    return fields[idx1 - 1] if fields and 0 < idx1 <= len(fields) else ""


def message_type(message: str) -> str:
    """MSH-9, e.g. 'ORM^O01' (empty if not found)."""
    f = _msh(message)
    return _get(f, 9) if f else ""


def type_family(message: str) -> str:
    """ADT / ORU / ORM / '' from the first component of MSH-9."""
    mt = message_type(message)
    fam = mt.split("^")[0].strip().upper() if mt else ""
    return fam if fam in REQUIRED_SEGMENTS else ""


def control_id(message: str) -> str:
    """MSH-10 message control id."""
    f = _msh(message)
    return _get(f, 10) if f else ""


def segment_names(message: str) -> list[str]:
    return [s[:3] for s in split_segments(message)]


def missing_required(message: str) -> list[str]:
    """Required segments absent for this message's type family (empty if OK or
    the family isn't one we validate)."""
    fam = type_family(message)
    if not fam:
        return []
    present = set(segment_names(message))
    return [s for s in REQUIRED_SEGMENTS[fam] if s not in present]


def new_control_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 9999):04d}"


def with_updated_msh(message: str, *, timestamp: bool = True,
                     control: bool = True) -> str:
    """Return the message with MSH-7 set to now and/or MSH-10 to a fresh id -
    so re-sends don't collide on control id and carry a current timestamp."""
    sep = field_sep(message)
    out = []
    for seg in split_segments(message):
        if seg.startswith("MSH"):
            f = seg.split(sep)
            # pad so indices exist
            while len(f) < 11:
                f.append("")
            if timestamp:
                f[6] = datetime.now().strftime("%Y%m%d%H%M%S")   # MSH-7
            if control:
                f[9] = new_control_id()                          # MSH-10
            seg = sep.join(f)
        out.append(seg)
    return "\r".join(out) + "\r"
