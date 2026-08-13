"""File-based DICOM tools: tag listing, header modify, multiframe split, dump.

These replace the legacy DCMTagLister, DCMModify, DCMSplitMF, DCMFolderDumper
and DumpExamData executables. All are pure-pydicom and need no network.
"""

from __future__ import annotations

import csv
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.tag import Tag
from pydicom.uid import generate_uid

from ..logging_setup import get_logger

log = get_logger("fileops")
Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _run_pool(files, fn, workers: int, stop: dict) -> None:
    """Run ``fn(file)`` over files, sequentially or across a thread pool.

    ``fn`` is responsible for its own thread-safe bookkeeping. ``stop`` is a
    ``{"v": bool}`` cancel flag checked between items in the sequential path.
    """
    if workers and workers > 1 and len(files) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(fn, files))
    else:
        for f in files:
            if stop.get("v"):
                break
            fn(f)


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(part: str) -> str:
    """Replace filesystem-illegal characters with underscore (legacy behavior)."""
    return _ILLEGAL.sub("_", str(part)).strip() or "UNKNOWN"


def find_dicom_files(root: Path, recursive: bool = True) -> list[Path]:
    """Return DICOM-looking files under root (by extension or DICM magic)."""
    root = Path(root)
    if root.is_file():
        return [root]
    it = root.rglob("*") if recursive else root.glob("*")
    files: list[Path] = []
    for p in it:
        if not p.is_file():
            continue
        if p.suffix.lower() in (".dcm", ".dic", ".ima", ""):
            files.append(p)
    return files


# ---------------------------------------------------------------------------
# Tag listing (DCMTagLister)
# ---------------------------------------------------------------------------
@dataclass
class TagRow:
    tag: str
    keyword: str
    vr: str
    value: str


def list_tags(path: Path, max_value_len: int = 120) -> list[TagRow]:
    ds = dcmread(str(path), force=True, stop_before_pixels=True)
    rows: list[TagRow] = []

    def walk(dataset: Dataset, prefix: str = "") -> None:
        for elem in dataset:
            tag_str = f"{prefix}({elem.tag.group:04X},{elem.tag.element:04X})"
            if elem.VR == "SQ":
                rows.append(TagRow(tag_str, elem.keyword or "", "SQ",
                                   f"<sequence, {len(elem.value)} item(s)>"))
                for i, item in enumerate(elem.value):
                    walk(item, prefix=f"{prefix}  item{i} ")
            else:
                val = str(elem.value)
                if len(val) > max_value_len:
                    val = val[:max_value_len] + "..."
                rows.append(TagRow(tag_str, elem.keyword or "",
                                   elem.VR or "", val))

    walk(ds)
    return rows


# ---------------------------------------------------------------------------
# Header modify (DCMModify)
# ---------------------------------------------------------------------------
@dataclass
class ModifyOp:
    """A single edit. action is 'set' or 'remove'. tag like '00100010'."""
    tag: str
    action: str = "set"
    value: str = ""


@dataclass
class ModifyResult:
    changed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_tag(tag: str) -> Tag:
    tag = tag.strip().replace(" ", "")
    if "," in tag:
        g, e = tag.strip("()").split(",")
        return Tag(int(g, 16), int(e, 16))
    if len(tag) == 8:
        return Tag(int(tag[:4], 16), int(tag[4:], 16))
    raise ValueError(f"Unrecognized tag format: {tag!r}")


def modify_files(files: Iterable[Path], ops: list[ModifyOp],
                 in_place: bool = False, out_dir: Path | None = None,
                 progress: Progress = _noop,
                 on_item=None, should_cancel=None,
                 workers: int = 1) -> ModifyResult:
    result = ModifyResult()
    files = [Path(f) for f in files]
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
        ok, detail = False, "ok"
        try:
            ds = dcmread(str(f), force=True)
            if "SOPClassUID" not in ds or "SOPInstanceUID" not in ds:
                raise ValueError("not a DICOM object (missing SOP UID)")
            for op in ops:
                tag = _parse_tag(op.tag)
                if op.action == "remove":
                    if tag in ds:
                        del ds[tag]
                else:  # set / update
                    if tag in ds:
                        ds[tag].value = op.value
                    else:
                        ds.add_new(tag, _guess_vr(tag), op.value)
            if in_place:
                dest = f
            else:
                base = out_dir or (f.parent / "modified")
                base.mkdir(parents=True, exist_ok=True)
                dest = base / f.name
            ds.save_as(str(dest))
            ok = True
            with lock:
                result.changed += 1
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            with lock:
                result.failed += 1
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {exc}")
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if on_item:
            on_item(n, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    return result


def _guess_vr(tag: Tag) -> str:
    try:
        from pydicom.datadict import dictionary_VR
        return dictionary_VR(tag)
    except KeyError:
        return "LO"


# ---------------------------------------------------------------------------
# Multiframe split (DCMSplitMF)
# ---------------------------------------------------------------------------
@dataclass
class SplitResult:
    frames_written: int = 0
    files_processed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def split_multiframe(files: Iterable[Path], out_dir: Path,
                     progress: Progress = _noop,
                     on_item=None, should_cancel=None,
                     workers: int = 1) -> SplitResult:
    result = SplitResult()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(x) for x in files]
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
            ds = dcmread(str(f), force=True)
            if "SOPClassUID" not in ds or "SOPInstanceUID" not in ds:
                raise ValueError("not a DICOM object (missing SOP UID)")
            n = int(getattr(ds, "NumberOfFrames", 1) or 1)
            if n <= 1:
                with lock:
                    result.skipped += 1
                detail = "skipped (single frame)"
            else:
                pixels = ds.pixel_array
                stem = f.stem
                for frame_idx in range(n):
                    frame = ds.copy()
                    frame.NumberOfFrames = 1
                    frame.SOPInstanceUID = generate_uid()
                    frame.InstanceNumber = frame_idx + 1
                    if getattr(frame, "file_meta", None) is not None:
                        frame.file_meta.MediaStorageSOPInstanceUID = \
                            frame.SOPInstanceUID
                    frame.PixelData = pixels[frame_idx].tobytes()
                    dest = out_dir / f"{stem}_frame{frame_idx + 1:04d}.dcm"
                    frame.save_as(str(dest))
                    with lock:
                        result.frames_written += 1
                with lock:
                    result.files_processed += 1
                detail = f"{n} frames"
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            ok = False
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            with lock:
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {exc}")
        with lock:
            counter["n"] += 1
            n2 = counter["n"]
        if on_item:
            on_item(n2, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    return result


# ---------------------------------------------------------------------------
# Folder / exam dump (DCMFolderDumper, DumpExamData)
# ---------------------------------------------------------------------------
DUMP_FIELDS = [
    "PatientID", "PatientName", "PatientBirthDate", "PatientSex",
    "StudyDate", "StudyTime", "AccessionNumber", "StudyDescription",
    "Modality", "SeriesNumber", "SeriesDescription", "InstanceNumber",
    "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
]


@dataclass
class DumpResult:
    rows: list[dict] = field(default_factory=list)
    files_read: int = 0
    errors: list[str] = field(default_factory=list)


def dump_files(files: Iterable[Path], fields: list[str] | None = None,
               progress: Progress = _noop,
               on_item=None, should_cancel=None,
               workers: int = 1) -> DumpResult:
    fields = fields or DUMP_FIELDS
    result = DumpResult()
    files = [Path(f) for f in files]
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
            ds = dcmread(str(f), force=True, stop_before_pixels=True)
            if "SOPClassUID" not in ds:
                raise ValueError("not a DICOM object (missing SOP Class UID)")
            row = {"File": str(f)}
            for fld in fields:
                row[fld] = str(getattr(ds, fld, ""))
            with lock:
                result.rows.append(row)
                result.files_read += 1
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            ok = False
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            with lock:
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {exc}")
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if on_item:
            on_item(n, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    progress(f"Read {result.files_read:,} file(s), {len(result.errors):,} "
             "error(s).")
    return result


def dump_folder(root: Path, fields: list[str] | None = None,
                recursive: bool = True, progress: Progress = _noop,
                on_item=None, should_cancel=None) -> DumpResult:
    files = find_dicom_files(root, recursive=recursive)
    progress(f"Scanning {len(files):,} file(s) ...")
    return dump_files(files, fields=fields, progress=progress,
                      on_item=on_item, should_cancel=should_cancel)


def write_dump_csv(result: DumpResult, out_csv: Path,
                   fields: list[str] | None = None) -> None:
    fields = fields or DUMP_FIELDS
    cols = ["File"] + fields
    with Path(out_csv).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.rows)


# ---------------------------------------------------------------------------
# De-identify files (file-side use of the receiver's Processor engine)
# ---------------------------------------------------------------------------
@dataclass
class DeidentResult:
    written: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def deidentify_files(files: Iterable[Path], cfg, out_dir: Path,
                     base_dir: Path | None = None,
                     progress: Progress = _noop,
                     on_item=None, should_cancel=None,
                     workers: int = 1) -> DeidentResult:
    """Apply the anonymize/morph/pixel-blank Processor to files, saving copies.

    ``cfg`` is a ``store.processing.ReceiverConfig``. Output structure mirrors
    the input relative to ``base_dir`` when given, else files land flat in
    ``out_dir`` (name collisions get a numeric suffix). ``workers`` > 1 runs the
    files through a thread pool (the Processor's UID remap is locked).
    """
    from ..store.processing import Processor  # lazy: avoids import cycle

    files = [Path(f) for f in files]
    total = len(files)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = Processor(cfg)
    result = DeidentResult()
    used: set[Path] = set()
    lock = threading.Lock()
    counter = {"n": 0}
    stop = {"v": False}

    def process(f):
        if stop["v"]:
            return
        if should_cancel is not None and should_cancel():
            stop["v"] = True
            return
        ok, detail = False, "ok"
        try:
            ds = dcmread(str(f), force=True)
            if "SOPClassUID" not in ds or "SOPInstanceUID" not in ds:
                raise ValueError("not a DICOM object (missing SOP UID)")
            proc.process(ds)
            if base_dir is not None:
                try:
                    rel = f.relative_to(base_dir)
                except ValueError:
                    rel = Path(f.name)
                dest = out_dir / rel
            else:
                dest = out_dir / f.name
            with lock:
                while dest in used or dest.resolve() == f.resolve():
                    dest = dest.with_stem(dest.stem + "_1")
                used.add(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            ds.save_as(str(dest), enforce_file_format=True)
            ok, detail = True, dest.name
            with lock:
                result.written += 1
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            detail = str(exc).strip().replace("\n", " ")[:200] or "error"
            with lock:
                result.failed += 1
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {exc}")
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if on_item:
            on_item(n, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    progress(f"Done. Wrote {result.written:,}, failed {result.failed:,}.")
    return result
