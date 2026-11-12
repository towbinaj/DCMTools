"""Build a DICOM media file-set (DICOMDIR) from loose files / folders.

Produces a standard-conformant file-set - the instances are copied into a
``DICOM/`` tree with short 8-character IDs and a ``DICOMDIR`` index at the root
- so the result imports cleanly as DICOM media into a VNA or workstation
(e.g. a Merge VNA or a Vitrea workstation). The *source* files are never
modified; everything is copied into a fresh output folder.

pydicom's :class:`~pydicom.fileset.FileSet` does the heavy lifting (the
directory records, the short file IDs, the conformant DICOMDIR). Its default
record builders are strict, though - they reject instances that are missing
type-2 record elements a real PACS export often omits (Study Time, Study ID,
Series Number, ...), and they need structured sequences for SR/RT objects. So
each instance is added defensively:

* try the default builder, back-filling any *missing scalar* record element
  with a safe placeholder (only ever on the in-memory copy, never the source),
* and if the default builder still can't cope (an exotic SOP class needing a
  sequence we can't synthesise), fall back to ``add_custom`` with a generic
  PATIENT -> STUDY -> SERIES -> record hierarchy so the instance is still
  indexed rather than dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.datadict import dictionary_VR, keyword_for_tag
from pydicom.fileset import FileSet, RecordNode
from pydicom.tag import Tag
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from ..logging_setup import get_logger
from .fileops import find_dicom_files

log = get_logger("dicomdir")
Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class DicomDirResult:
    added: int = 0
    skipped: int = 0
    custom: int = 0            # how many needed the add_custom fallback
    out_dir: Path | None = None
    dicomdir: Path | None = None
    patients: int = 0
    studies: int = 0
    series: int = 0
    errors: list[str] = field(default_factory=list)


# "The instance's (0008,0030) 'Study Time' element is missing / cannot be empty"
_MISSING_RE = re.compile(r"\((?P<g>[0-9A-Fa-f]{4}),(?P<e>[0-9A-Fa-f]{4})\)")
# VRs we can fabricate a harmless placeholder for (string/number scalars).
_SYNTH_VR = {"DA", "TM", "DT", "IS", "DS", "SH", "LO", "CS", "AE", "PN",
             "ST", "LT", "UT", "AS", "US", "UL", "SS", "SL", "FL", "FD", "UI"}


def _placeholder(tag: Tag):
    """A benign stand-in for a missing *record* element, or None if its VR is
    not a simple scalar (e.g. a sequence - we can't synthesise those)."""
    try:
        vr = dictionary_VR(tag)
    except KeyError:
        vr = "LO"
    if vr == "DA":
        return "19000101"
    if vr == "TM":
        return "000000"
    if vr == "DT":
        return "19000101000000"
    if vr in ("IS", "DS", "US", "UL", "SS", "SL", "FL", "FD"):
        return "0"
    if vr == "UI":
        return generate_uid()
    if vr in _SYNTH_VR:
        return "UNKNOWN"
    return None


def _rec(record_type: str, **kw) -> Dataset:
    r = Dataset()
    r.DirectoryRecordType = record_type
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _generic_leaf(ds: Dataset) -> RecordNode:
    """A PATIENT -> STUDY -> SERIES -> (generic) record chain for ``ds``.

    Used as the ``add_custom`` fallback when the default record builder can't
    handle the SOP class. Record fields are filled from the instance, with
    placeholders where absent; the leaf references the file by its real SOP
    Class / Instance / Transfer Syntax UIDs so viewers resolve it correctly.
    """
    ts = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None) \
        or ExplicitVRLittleEndian
    patient = RecordNode(_rec(
        "PATIENT",
        PatientID=str(ds.get("PatientID", "") or "NOID"),
        PatientName=str(ds.get("PatientName", "") or "UNKNOWN")))
    study = RecordNode(_rec(
        "STUDY",
        StudyInstanceUID=ds.StudyInstanceUID,
        StudyDate=str(ds.get("StudyDate", "")),
        StudyTime=str(ds.get("StudyTime", "")),
        StudyID=str(ds.get("StudyID", "")),
        AccessionNumber=str(ds.get("AccessionNumber", ""))))
    series = RecordNode(_rec(
        "SERIES",
        Modality=str(ds.get("Modality", "") or "OT"),
        SeriesInstanceUID=ds.SeriesInstanceUID,
        SeriesNumber=str(ds.get("SeriesNumber", ""))))
    leaf = RecordNode(_rec(
        "IMAGE",
        InstanceNumber=str(ds.get("InstanceNumber", "")),
        ReferencedSOPClassUIDInFile=ds.SOPClassUID,
        ReferencedSOPInstanceUIDInFile=ds.SOPInstanceUID,
        ReferencedTransferSyntaxUIDInFile=ts))
    study.parent = patient
    series.parent = study
    leaf.parent = series
    return leaf


def _add_instance(fs: FileSet, ds: Dataset) -> tuple[str, list[str]]:
    """Add ``ds`` to ``fs``; return (how, synthesized_keywords).

    ``how`` is "default" (standard record builder) or "custom" (fallback).
    Any record elements we had to invent are returned so the caller can note
    it. Raises only if even the fallback fails (e.g. missing SOP/Study UID).
    """
    synth: list[str] = []
    for _ in range(16):   # bounded: one element fixed per miss, then retry
        try:
            fs.add(ds)
            return "default", synth
        except ValueError as exc:
            m = _MISSING_RE.search(str(exc))
            if not m:
                break
            tag = Tag(int(m["g"], 16), int(m["e"], 16))
            ph = _placeholder(tag)
            kw = keyword_for_tag(tag)
            if ph is None or not kw:
                break   # not a synthesisable scalar - use the fallback
            setattr(ds, kw, ph)
            synth.append(kw)
    fs.add_custom(ds, _generic_leaf(ds))
    return "custom", synth


def _clean_fileset_id(raw: str) -> str:
    """A valid File-set ID: uppercase A-Z/0-9/_ , 16 chars max."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", str(raw or "")).upper()[:16]
    return cleaned or "DCMTOOLKIT"


def unique_dir(parent: Path, name: str) -> Path:
    """A not-yet-existing child of ``parent`` based on ``name``."""
    parent = Path(parent)
    base = parent / name
    out, n = base, 1
    while out.exists():
        out = parent / f"{name}_{n}"
        n += 1
    return out


def make_dicomdir(sources: Iterable[Path], out_dir: Path, *,
                  fileset_id: str = "DCMTOOLKIT",
                  progress: Progress = _noop,
                  on_item=None, should_cancel=None) -> DicomDirResult:
    """Copy the DICOM instances under ``sources`` into ``out_dir`` as a
    conformant media file-set with a ``DICOMDIR`` index.

    ``sources`` may mix files and folders (folders are searched recursively).
    ``out_dir`` must not already exist or must be empty - it is created and
    populated; the sources are left untouched. ``on_item`` follows the uniform
    ``(index, total, path, ok, detail)`` batch callback.
    """
    files: list[Path] = []
    for s in sources:
        files.extend(find_dicom_files(Path(s)))
    # Never fold an existing DICOMDIR index into the new one.
    files = [f for f in files if f.name.upper() != "DICOMDIR"]
    # De-dup while preserving order.
    files = list(dict.fromkeys(files))

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"Output folder is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = DicomDirResult(out_dir=out_dir)
    fs = FileSet()
    try:
        fs.ID = _clean_fileset_id(fileset_id)
    except Exception:  # noqa: BLE001 - ID is cosmetic; never fail the build
        pass

    total = len(files)
    progress(f"Indexing {total:,} file(s) ...")
    for n, f in enumerate(files, 1):
        if should_cancel is not None and should_cancel():
            break
        ok, detail = True, "added"
        try:
            ds = dcmread(str(f), force=True)
            for req in ("SOPInstanceUID", "SOPClassUID",
                        "StudyInstanceUID", "SeriesInstanceUID"):
                if req not in ds or not ds.get(req):
                    raise ValueError(f"not a DICOM instance (no {req})")
            how, synth = _add_instance(fs, ds)
            result.added += 1
            if how == "custom":
                result.custom += 1
            detail = how + (f" (+{len(synth)} synth)" if synth else "")
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            ok = False
            result.skipped += 1
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            if len(result.errors) < 5000:
                result.errors.append(f"{f}: {exc}")
        if on_item is not None:
            on_item(n, total, f, ok, detail)

    if result.added and not (should_cancel is not None and should_cancel()):
        progress("Writing file-set + DICOMDIR ...")
        fs.write(str(out_dir))
        result.dicomdir = out_dir / "DICOMDIR"
        result.patients = len({i.PatientID for i in fs})
        result.studies = len({i.StudyInstanceUID for i in fs})
        result.series = len({i.SeriesInstanceUID for i in fs})
        progress(f"Done: {result.added:,} instance(s) -> {result.patients:,} "
                 f"patient(s), {result.studies:,} study(ies), "
                 f"{result.series:,} series. DICOMDIR at {result.dicomdir}")
    else:
        progress("Nothing written (no valid instances or cancelled).")
    return result
