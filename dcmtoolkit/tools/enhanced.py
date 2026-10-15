"""Enhanced (multi-frame) DICOM -> classic single-frame conversion.

Enhanced CT/MR/PET objects pack all frames into one instance and keep the
per-frame attributes in Shared/Per-Frame Functional Group Sequences rather than
at the top level. This module converts them to classic single-frame instances:
one file per frame, the classic SOP Class, and the functional-group macros
flattened down to the classic top-level attributes a legacy viewer/PACS expects
(geometry, pixel spacing, windowing, rescale, image type, ...).

Pixel data is split without any decoder and without quality loss: native
(uncompressed) frames are sliced by length; encapsulated (JPEG/JPEG2000/RLE)
frames are extracted and re-encapsulated single-frame, preserving the original
transfer syntax.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, PYDICOM_IMPLEMENTATION_UID

from ..logging_setup import get_logger
from .fileops import _run_pool

log = get_logger("enhanced")

# Enhanced (and Legacy-Converted Enhanced) SOP Class -> classic SOP Class.
ENHANCED_TO_CLASSIC = {
    "1.2.840.10008.5.1.4.1.1.2.1": "1.2.840.10008.5.1.4.1.1.2",    # Enh CT -> CT
    "1.2.840.10008.5.1.4.1.1.2.2": "1.2.840.10008.5.1.4.1.1.2",    # LegacyConv CT
    "1.2.840.10008.5.1.4.1.1.4.1": "1.2.840.10008.5.1.4.1.1.4",    # Enh MR -> MR
    "1.2.840.10008.5.1.4.1.1.4.4": "1.2.840.10008.5.1.4.1.1.4",    # LegacyConv MR
    "1.2.840.10008.5.1.4.1.1.130": "1.2.840.10008.5.1.4.1.1.128",  # Enh PET -> PET
    "1.2.840.10008.5.1.4.1.1.128.1": "1.2.840.10008.5.1.4.1.1.128",  # LegacyConv PET
}

# Enhanced-only elements that must not appear in a classic instance.
_DROP = [
    "NumberOfFrames", "FrameIncrementPointer",
    "SharedFunctionalGroupsSequence", "PerFrameFunctionalGroupsSequence",
    "DimensionOrganizationSequence", "DimensionIndexSequence",
    "DimensionOrganizationType", "ConcatenationUID", "InConcatenationNumber",
    "InConcatenationTotalNumber", "ConcatenationFrameOffsetNumber",
    "PixelData",
]


@dataclass
class ConvertResult:
    files_in: int = 0
    converted: int = 0          # source enhanced files converted
    frames_written: int = 0     # classic instances produced
    skipped: int = 0            # not enhanced / unsupported
    errors: list[str] = field(default_factory=list)


def is_enhanced(ds: Dataset) -> bool:
    return str(getattr(ds, "SOPClassUID", "")) in ENHANCED_TO_CLASSIC \
        and "PerFrameFunctionalGroupsSequence" in ds


# Attributes whose classic tag differs from the enhanced/functional-group tag,
# so they land in the proper classic attribute (rest keep their own tag).
_RENAME = {
    "FrameType": "ImageType",            # CT/MR/PET frame type -> ImageType
    "FrameLaterality": "ImageLaterality",
    "EffectiveEchoTime": "EchoTime",
}


def _is_enhanced_specific(tag) -> bool:
    """True for attributes that belong only to enhanced/functional-group IODs,
    so they have no place in a classic single-frame object. Elements >= 0x9000
    are the multi-frame/functional-group additions; groups 5200/5260 are the
    functional-group sequences themselves."""
    return tag.element >= 0x9000 or tag.group in (0x5200, 0x5260)


def _flat_set(target: Dataset, child) -> None:
    kw = child.keyword
    if kw in _RENAME:
        try:
            setattr(target, _RENAME[kw], child.value)   # into its classic tag
        except Exception:  # noqa: BLE001 - skip if VR/value incompatible
            pass
        return
    tag = child.tag
    if tag.element == 0x0000 or _is_enhanced_specific(tag):
        return   # group-length or enhanced-specific -> not carried over
    target[tag] = copy.deepcopy(child)


def _flatten(target: Dataset, fg_item) -> None:
    """Flatten a functional-group item onto the classic top level: carry every
    standard-compatible attribute (one level into each macro sequence), map the
    renamed ones to their classic tag, and drop enhanced-specific attributes."""
    if fg_item is None:
        return
    for elem in fg_item:
        if elem.VR == "SQ":
            if elem.value:
                for child in elem.value[0]:
                    _flat_set(target, child)
        else:
            _flat_set(target, elem)


def _frame_bytes_fn(ds: Dataset, n_frames: int):
    """Return (get_frame(i) -> bytes, pixel_vr, is_encapsulated)."""
    ts = ds.file_meta.TransferSyntaxUID
    if ts.is_encapsulated:
        from pydicom.encaps import get_frame, encapsulate
        raw = ds.PixelData

        def get(i):
            return encapsulate([get_frame(raw, i, number_of_frames=n_frames)])

        return get, "OB", True

    rows = int(ds.Rows)
    cols = int(ds.Columns)
    spp = int(getattr(ds, "SamplesPerPixel", 1))
    bits = int(ds.BitsAllocated)
    frame_len = rows * cols * spp * (bits // 8)
    raw = ds.PixelData
    pixel_vr = ds["PixelData"].VR

    def get(i):
        b = raw[i * frame_len:(i + 1) * frame_len]
        if len(b) % 2:
            b += b"\x00"
        return b

    return get, pixel_vr, False


def _build_base(ds: Dataset) -> Dataset:
    """Top-level attributes minus enhanced-only bulk and functional groups."""
    base = Dataset()
    drop = set(_DROP)
    for elem in ds:
        if elem.keyword in drop or _is_enhanced_specific(elem.tag):
            continue
        base.add(elem)
    return copy.deepcopy(base)


def convert_file(src: Path, out_dir: Path, *, new_series: bool = True) -> tuple[int, str]:
    """Convert one enhanced file to classic single-frame files in ``out_dir``.

    Every standard-compatible functional-group attribute is flattened onto the
    classic top level; enhanced-specific attributes are dropped. Returns
    ``(frames_written, detail)``; 0 means skipped.
    """
    ds = dcmread(str(src), force=True)
    sop_class = str(getattr(ds, "SOPClassUID", ""))
    classic = ENHANCED_TO_CLASSIC.get(sop_class)
    if classic is None:
        return 0, f"skipped (not enhanced CT/MR/PET: {sop_class or 'no SOP'})"
    if "PerFrameFunctionalGroupsSequence" not in ds:
        return 0, "skipped (no per-frame functional groups)"

    n = int(getattr(ds, "NumberOfFrames", 0) or 0)
    perframe = ds.PerFrameFunctionalGroupsSequence
    if n <= 0:
        n = len(perframe)
    shared_seq = getattr(ds, "SharedFunctionalGroupsSequence", None)
    shared = shared_seq[0] if shared_seq else None

    base = _build_base(ds)
    _flatten(base, shared)   # shared attrs onto the template

    get_frame_bytes, pixel_vr, encapsulated = _frame_bytes_fn(ds, n)
    ts = ds.file_meta.TransferSyntaxUID
    series_uid = generate_uid() if new_series else str(ds.SeriesInstanceUID)
    src_class = sop_class
    src_inst = str(getattr(ds, "SOPInstanceUID", ""))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for i in range(n):
        fds = copy.deepcopy(base)
        fg = perframe[i] if i < len(perframe) else None
        _flatten(fds, fg)   # per-frame overrides shared

        # Identity
        fds.SOPClassUID = classic
        sop_inst = generate_uid()
        fds.SOPInstanceUID = sop_inst
        fds.SeriesInstanceUID = series_uid
        # Instance number: in-stack position if available, else frame order.
        inst_no = i + 1
        fc = getattr(fg, "FrameContentSequence", None) if fg else None
        if fc and "InStackPositionNumber" in fc[0]:
            try:
                inst_no = int(fc[0].InStackPositionNumber)
            except (TypeError, ValueError):
                inst_no = i + 1
        fds.InstanceNumber = inst_no
        if fc and "FrameAcquisitionNumber" in fc[0]:
            try:
                fds.AcquisitionNumber = int(fc[0].FrameAcquisitionNumber)
            except (TypeError, ValueError):
                pass
        if "ImageType" not in fds:
            fds.ImageType = ["DERIVED", "SECONDARY"]
        # Provenance: point back at the enhanced source.
        ref = Dataset()
        ref.ReferencedSOPClassUID = src_class
        ref.ReferencedSOPInstanceUID = src_inst
        fds.SourceImageSequence = [ref]

        # Pixel data for this frame
        fds.PixelData = get_frame_bytes(i)
        fds["PixelData"].VR = pixel_vr

        # Fresh file meta for the classic instance
        fm = FileMetaDataset()
        fm.MediaStorageSOPClassUID = classic
        fm.MediaStorageSOPInstanceUID = sop_inst
        fm.TransferSyntaxUID = ts
        fm.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
        fds.file_meta = fm

        dest = out_dir / f"{src.stem}_{i + 1:04d}.dcm"
        fds.save_as(str(dest), enforce_file_format=True)
        written += 1
    return written, f"{written} frame(s) -> classic"


def convert_enhanced(files, out_dir: Path, *, new_series: bool = True,
                     on_item=None, should_cancel=None,
                     workers: int = 1) -> ConvertResult:
    files = [Path(f) for f in files]
    result = ConvertResult(files_in=len(files))
    total = len(files)
    lock = threading.Lock()
    counter = {"n": 0}
    stop = {"v": False}

    def process(f):
        if stop["v"]:
            return
        if should_cancel is not None and should_cancel():
            stop["v"] = True
            return
        ok, detail = True, "ok"
        try:
            written, detail = convert_file(f, out_dir, new_series=new_series)
            with lock:
                if written:
                    result.converted += 1
                    result.frames_written += written
                else:
                    result.skipped += 1
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            ok = False
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            with lock:
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {detail}")
        with lock:
            counter["n"] += 1
            idx = counter["n"]
        if on_item:
            on_item(idx, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    return result
