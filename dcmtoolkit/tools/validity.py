"""Per-tag validity status shared by the Tag Lister and Tag Compare tools.

Four buckets:
  valid - value conforms to its VR (and, when IOD-checked, no conformance issue)
  error - bad VR value, or TagEmpty / TagNotAllowed / EnumValueNotAllowed
  warn  - present but not defined for the IOD (TagUnexpected)
  na    - not evaluated (sequences, empty optional)
"""

from __future__ import annotations

from . import dicomtags

CATEGORIES = ["valid", "error", "warn", "na"]
LABELS = {"valid": "Valid", "error": "Error", "warn": "Not in IOD", "na": "N/A"}
ICONS = {"valid": "✓", "error": "✗", "warn": "⚠", "na": "·"}

_ERROR_CODES = {"TagEmpty", "TagNotAllowed", "EnumValueNotAllowed"}


def status(vr: str, value: str, conf_code: str | None = None) -> str:
    """Validity bucket for one element. ``conf_code`` is an IOD conformance code
    for this tag (from conformance.conformance_issues), or None."""
    if conf_code == "TagUnexpected":
        return "warn"
    if conf_code in _ERROR_CODES:
        return "error"
    if (vr or "") == "SQ":
        return "na"
    ok, _ = dicomtags.validate(vr or "", value or "")
    return "valid" if ok else "error"


def worst(a: str, b: str) -> str:
    """The more-severe of two statuses (error > warn > valid > na)."""
    for s in ("error", "warn", "valid"):
        if a == s or b == s:
            return s
    return "na"
