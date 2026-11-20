"""Structural sanitize: detect and strip smuggled-payload carriers in DICOM.

Sanitization is *not* de-identification. De-identification removes **who** an
object is about (PHI). Sanitization removes **what an object can carry** - the
non-clinical binary elements a DICOM file can use to smuggle a payload into a
downstream parser, image decoder, PACS, viewer, or router. A de-identified object
can still be a malware carrier; "we de-id'd it" is not "we made it safe."

Carriers handled (all at known, fixed locations, so they strip deterministically):

* 128-byte preamble polyglot (PE/MZ, ELF, PDF, ZIP) - CVE-2019-11687
* Icon Image Sequence (0088,0200) - a second pixel blob
* Overlay planes (groups 6000-60FF, even) - arbitrary bitplanes
* Curve data (groups 5000-50FF, even, retired) - arbitrary binary
* Encapsulated documents (0042,0011) - an embedded PDF/CDA/STL/...

This deliberately does NOT vet *primary* pixel data: a crafted JPEG/JPEG2000/RLE
bitstream that exploits a decoder is not detectable by inspection (the only robust
control there is sandboxed transcoding). It is attack-surface reduction, not a
malware scanner, and it does not protect the host that parses the input.

Preamble zeroing stays an unconditional invariant elsewhere (``fileops.save_dicom``
and ``Processor.process`` always write a clean zero preamble); this module only
*detects and reports* a non-zero one. Overlays, curves and icon images can be
legitimate clinical content, so stripping is opt-in (``SanitizeConfig.enabled``,
default off). Findings are counts-only - carrier values can be PHI and must never
be logged.

Ported from the De-ID app (v0.7.0, ``deid/engine/sanitize.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydicom.dataset import Dataset

# Tags / groups we detect and (optionally) strip.
_ICON_IMAGE_SEQUENCE = 0x00880200
_ENCAPSULATED_DOCUMENT = 0x00420011
_OVERLAY_GROUP_LO, _OVERLAY_GROUP_HI = 0x6000, 0x60FF
_CURVE_GROUP_LO, _CURVE_GROUP_HI = 0x5000, 0x50FF

# SOP Classes whose primary payload IS an embedded document (no clinical image).
_ENCAPSULATED_SOP_CLASSES = frozenset({
    "1.2.840.10008.5.1.4.1.1.104.1",   # Encapsulated PDF
    "1.2.840.10008.5.1.4.1.1.104.2",   # Encapsulated CDA
    "1.2.840.10008.5.1.4.1.1.104.3",   # Encapsulated STL
    "1.2.840.10008.5.1.4.1.1.104.4",   # Encapsulated OBJ
    "1.2.840.10008.5.1.4.1.1.104.5",   # Encapsulated MTL
})


@dataclass
class SanitizeConfig:
    """Opt-in structural sanitize. Default off: overlays/curves/icons can be
    legitimate clinical content, so this is a conscious "untrusted source" choice.
    The preamble is always neutralized on write regardless of this toggle."""
    enabled: bool = False
    strip_icon_images: bool = True
    strip_overlays: bool = True
    strip_curves: bool = True
    strip_encapsulated: bool = True


@dataclass
class SanitizeReport:
    preamble_payload: bool = False
    preamble_signature: str = ""
    icon_sequences: int = 0
    overlay_groups: int = 0
    curve_groups: int = 0
    encapsulated_docs: int = 0
    is_encapsulated_object: bool = False
    stripped_tags: list = field(default_factory=list)

    @property
    def any_found(self) -> bool:
        return bool(self.preamble_payload or self.icon_sequences
                    or self.overlay_groups or self.curve_groups
                    or self.encapsulated_docs or self.is_encapsulated_object)

    def findings(self) -> list[str]:
        """Counts-only, human-readable lines. Never includes element values."""
        out: list[str] = []
        if self.preamble_payload:
            out.append(f"non-empty preamble ({self.preamble_signature or 'non-zero payload'})")
        if self.icon_sequences:
            out.append(f"{self.icon_sequences} icon image sequence(s)")
        if self.overlay_groups:
            out.append(f"{self.overlay_groups} overlay plane group(s)")
        if self.curve_groups:
            out.append(f"{self.curve_groups} curve data group(s)")
        if self.encapsulated_docs:
            out.append(f"{self.encapsulated_docs} encapsulated document(s)")
        if self.is_encapsulated_object:
            out.append("object SOP Class is an encapsulated document "
                       "(consider a drop/quarantine rule)")
        return out


def _preamble_signature(preamble: bytes) -> str:
    if preamble[:2] == b"MZ":
        return "MZ (DOS/PE executable header)"
    if preamble[:4] == b"\x7fELF":
        return "ELF (executable)"
    if preamble[:4] == b"%PDF":
        return "PDF header"
    if preamble[:2] == b"PK":
        return "ZIP/PK archive header"
    return "non-zero payload"


def detect_preamble(ds: Dataset, report: SanitizeReport) -> None:
    """Record whether the 128-byte preamble carries a payload (does not zero it -
    that is the unconditional invariant kept in the write path)."""
    current = getattr(ds, "preamble", None)
    if current and any(current):
        report.preamble_payload = True
        report.preamble_signature = _preamble_signature(bytes(current))


def _carriers(ds: Dataset):
    """Yield (kind, tag) for each top-level carrier element present in ``ds``.
    kind is one of 'icon' | 'encap' | 'overlay' | 'curve'."""
    for elem in ds:
        tag = elem.tag
        group = tag.group
        t = int(tag)
        if t == _ICON_IMAGE_SEQUENCE:
            yield "icon", tag
        elif t == _ENCAPSULATED_DOCUMENT:
            yield "encap", tag
        elif _OVERLAY_GROUP_LO <= group <= _OVERLAY_GROUP_HI and group % 2 == 0:
            yield "overlay", tag
        elif _CURVE_GROUP_LO <= group <= _CURVE_GROUP_HI and group % 2 == 0:
            yield "curve", tag


def scan_dataset(ds: Dataset) -> SanitizeReport:
    """Detect carriers (and a payload preamble) WITHOUT modifying ``ds``.

    For read-only auditing (e.g. the QA tool). Overlay/curve planes are counted
    as distinct groups, so a group with several elements counts once.
    """
    report = SanitizeReport()
    detect_preamble(ds, report)
    if str(getattr(ds, "SOPClassUID", "") or "") in _ENCAPSULATED_SOP_CLASSES:
        report.is_encapsulated_object = True
    overlay_groups: set[int] = set()
    curve_groups: set[int] = set()
    for kind, tag in _carriers(ds):
        if kind == "icon":
            report.icon_sequences += 1
        elif kind == "encap":
            report.encapsulated_docs += 1
        elif kind == "overlay":
            overlay_groups.add(tag.group)
        elif kind == "curve":
            curve_groups.add(tag.group)
    report.overlay_groups = len(overlay_groups)
    report.curve_groups = len(curve_groups)
    return report


def sanitize_dataset(ds: Dataset, cfg: SanitizeConfig | None = None) -> SanitizeReport:
    """Strip identifiable smuggled-payload carriers from ``ds`` in place.

    Always detects (returns a populated report); strips only the roles enabled in
    ``cfg``. With ``cfg`` None or ``enabled`` False it detects but strips nothing,
    so callers can report on a disabled pass. Primary ``PixelData`` is never
    touched. The preamble is not zeroed here - that is the write-path invariant.
    """
    report = scan_dataset(ds)
    if cfg is None or not cfg.enabled:
        return report

    # Collect first - never mutate while iterating.
    to_delete = []
    for kind, tag in _carriers(ds):
        if kind == "icon" and cfg.strip_icon_images:
            to_delete.append(tag)
        elif kind == "encap" and cfg.strip_encapsulated:
            to_delete.append(tag)
        elif kind == "overlay" and cfg.strip_overlays:
            to_delete.append(tag)
        elif kind == "curve" and cfg.strip_curves:
            to_delete.append(tag)
    for tag in to_delete:
        try:
            del ds[tag]
            report.stripped_tags.append(f"{tag.group:04X}{tag.element:04X}")
        except KeyError:
            pass
    return report
