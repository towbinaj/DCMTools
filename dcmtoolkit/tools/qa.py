"""QA / consistency checks over a set of DICOM files.

Reads headers in parallel and reports:
  * incongruent - tags that should be uniform within a patient/study/series but
    have more than one value across the files;
  * abnormal    - values that violate their DICOM VR (bad dates, length, etc.);
  * missing     - files missing a Type-1 required tag;
  * duplicate   - the same SOPInstanceUID in more than one file;
  * mismatch    - a folder that mixes multiple studies or series.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread

from . import dicomtags
from . import conformance as _conformance
from . import render as _render
from .fileops import _run_pool

# keyword -> VR (for the abnormal-value validation)
_FIELDS = {
    "PatientID": "LO", "PatientName": "PN", "PatientBirthDate": "DA",
    "PatientSex": "CS", "PatientAge": "AS", "PatientBirthTime": "TM",
    "StudyInstanceUID": "UI", "StudyDate": "DA", "StudyTime": "TM",
    "AccessionNumber": "SH", "StudyDescription": "LO", "StudyID": "SH",
    "ReferringPhysicianName": "PN",
    "SeriesInstanceUID": "UI", "SeriesNumber": "IS", "Modality": "CS",
    "SeriesDescription": "LO", "SeriesDate": "DA", "SeriesTime": "TM",
    "BodyPartExamined": "CS",
    "SOPClassUID": "UI", "SOPInstanceUID": "UI",
}
STUDY_UNIFORM = ["PatientID", "PatientName", "PatientBirthDate", "PatientSex",
                 "StudyDate", "StudyTime", "AccessionNumber",
                 "StudyDescription", "StudyID", "ReferringPhysicianName"]
SERIES_UNIFORM = ["Modality", "SeriesNumber", "SeriesDescription",
                  "SeriesDate", "SeriesTime", "BodyPartExamined"]
PATIENT_UNIFORM = ["PatientName", "PatientBirthDate", "PatientSex"]
REQUIRED_TYPE1 = ["SOPClassUID", "SOPInstanceUID", "StudyInstanceUID",
                  "SeriesInstanceUID"]


@dataclass
class Finding:
    category: str      # incongruent | abnormal | missing | duplicate | mismatch
    summary: str
    detail: str


@dataclass
class QAResult:
    files_read: int = 0
    findings: list[Finding] = field(default_factory=list)


def _short(uid: str) -> str:
    uid = str(uid)
    return uid if len(uid) <= 24 else "..." + uid[-20:]


def _uid_name(uid: str) -> str:
    """Human name for a transfer-syntax / SOP-class UID, else the UID itself."""
    try:
        from pydicom.uid import UID
        n = UID(str(uid)).name
        return n if n and n != str(uid) else str(uid)
    except Exception:  # noqa: BLE001
        return str(uid)


def qa_scan(files, on_item=None, should_cancel=None, workers: int = 1,
            conformance: bool = False, sendability: bool = False,
            render: bool = False) -> QAResult:
    files = [Path(f) for f in files]
    total = len(files)
    records: list[dict] = []
    lock = threading.Lock()
    counter = {"n": 0}
    stop = {"v": False}
    # Load the standard once up front so the parallel workers don't race on it.
    if conformance:
        _conformance.available()

    def process(f):
        if stop["v"]:
            return
        if should_cancel is not None and should_cancel():
            stop["v"] = True
            return
        ok, detail = True, "ok"
        try:
            # Pixels are only needed for the render check.
            ds = dcmread(str(f), force=True, stop_before_pixels=not render)
            rec = {"__file": f}
            for kw in _FIELDS:
                v = ds.get(kw)
                rec[kw] = "" if v is None else str(v)
            # DICOMDIR and other Media Storage Directory files are not image
            # instances - exclude them from the instance-level checks.
            fm = getattr(ds, "file_meta", None)
            ts = getattr(fm, "TransferSyntaxUID", "") if fm is not None else ""
            rec["__ts"] = str(ts)
            rec["__ts_name"] = str(getattr(ts, "name", "") or "")
            msc = str(getattr(fm, "MediaStorageSOPClassUID", "") or "") \
                if fm is not None else ""
            if msc == "1.2.840.10008.1.3.10" or f.name.upper() == "DICOMDIR":
                rec["__skip"] = "DICOMDIR / media directory"
            else:
                if conformance:
                    rec["__conf"] = _conformance.conformance_errors(ds)
                if render:
                    rec["__render"] = _render.render_errors(ds)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc).strip()[:120]
            rec = {"__file": f, "__unreadable": detail}
        with lock:
            records.append(rec)
            counter["n"] += 1
            n = counter["n"]
        if on_item:
            on_item(n, total, f, ok, detail)

    _run_pool(files, process, workers, stop)

    findings: list[Finding] = []
    good = [r for r in records
            if not r.get("__unreadable") and not r.get("__skip")]

    for r in records:
        if r.get("__unreadable"):
            findings.append(Finding(
                "abnormal", f"Unreadable: {Path(r['__file']).name}",
                r["__unreadable"]))

    # missing Type-1
    for r in good:
        miss = [kw for kw in REQUIRED_TYPE1 if not r.get(kw)]
        if miss:
            findings.append(Finding(
                "missing", f"{Path(r['__file']).name}: missing "
                f"{', '.join(miss)}", "Type-1 required tag(s) absent"))

    # abnormal values
    for r in good:
        for kw, vr in _FIELDS.items():
            val = r.get(kw, "")
            if not val:
                continue
            okv, msg = dicomtags.validate(vr, val)
            if not okv:
                findings.append(Finding(
                    "abnormal", f"{Path(r['__file']).name}: {kw} invalid",
                    f"{kw} = {val!r}  ->  {msg}"))

    # IOD conformance (dicom-validator) - only when requested
    for r in good:
        errs = r.get("__conf")
        if errs:
            name = Path(r["__file"]).name
            findings.append(Finding(
                "conformance",
                f"{name}: {len(errs)} IOD conformance issue(s)",
                "  |  ".join(errs)))   # full list (not truncated) for triage

    # Render / pixel-decode - only when requested
    for r in good:
        errs = r.get("__render")
        if errs:
            name = Path(r["__file"]).name
            findings.append(Finding(
                "render", f"{name}: will not render",
                "; ".join(errs)[:500]))

    # incongruent (grouped)
    def check(group_field, uniform, level):
        groups: dict[str, list[dict]] = {}
        for r in good:
            k = r.get(group_field, "")
            if k:
                groups.setdefault(k, []).append(r)
        for k, rs in groups.items():
            for kw in uniform:
                by_val: dict[str, list[str]] = {}
                for r in rs:
                    v = r.get(kw, "") or "(blank)"
                    by_val.setdefault(v, []).append(Path(r["__file"]).name)
                if len(by_val) < 2:
                    continue
                # Most common value first, so outliers are easy to see.
                parts = []
                for v, names in sorted(by_val.items(),
                                       key=lambda x: -len(x[1])):
                    ex = ", ".join(names[:2])
                    more = f" +{len(names) - 2}" if len(names) > 2 else ""
                    parts.append(f"{v}  [{len(names)} file(s): {ex}{more}]")
                findings.append(Finding(
                    "incongruent",
                    f"{level} {_short(k)}: {kw} differs "
                    f"({len(by_val)} values, {len(rs)} files)",
                    "  |  ".join(parts)[:500]))

    check("StudyInstanceUID", STUDY_UNIFORM, "Study")
    check("SeriesInstanceUID", SERIES_UNIFORM, "Series")
    check("PatientID", PATIENT_UNIFORM, "Patient")

    # duplicate SOP instance UIDs
    sop: dict[str, list[str]] = {}
    for r in good:
        s = r.get("SOPInstanceUID", "")
        if s:
            sop.setdefault(s, []).append(Path(r["__file"]).name)
    for s, fs in sop.items():
        if len(fs) > 1:
            findings.append(Finding(
                "duplicate", f"Duplicate SOPInstanceUID in {len(fs)} files",
                f"{_short(s)}: " + ", ".join(fs[:8])))

    # folder mixes multiple studies / series
    folders: dict[str, list[dict]] = {}
    for r in good:
        folders.setdefault(str(Path(r["__file"]).parent), []).append(r)
    for fo, rs in folders.items():
        studies = {r.get("StudyInstanceUID", "") for r in rs
                   if r.get("StudyInstanceUID")}
        if len(studies) > 1:
            findings.append(Finding(
                "mismatch", f"Folder mixes {len(studies)} studies: "
                f"{Path(fo).name}",
                "; ".join(_short(s) for s in list(studies)[:5])))
        series = {r.get("SeriesInstanceUID", "") for r in rs
                  if r.get("SeriesInstanceUID")}
        if len(series) > 1:
            findings.append(Finding(
                "mismatch", f"Folder mixes {len(series)} series: "
                f"{Path(fo).name}",
                "; ".join(_short(s) for s in list(series)[:5])))

    # Transfer-syntax and SOP-class distribution. A receiver negotiates a
    # *presentation context* = SOP class + transfer syntax, so this is the first
    # thing to check when a strict VNA rejects objects a lenient PACS accepts.
    if good:
        from collections import Counter
        ts_counts: Counter = Counter()
        ts_uid: dict[str, str] = {}
        for r in good:
            label = r.get("__ts_name") or r.get("__ts") or "(unknown)"
            ts_counts[label] += 1
            ts_uid.setdefault(label, r.get("__ts", ""))
        for label, n in sorted(ts_counts.items(), key=lambda x: -x[1]):
            findings.append(Finding(
                "transfer", f"Transfer syntax: {label}  ({n} file(s))",
                f"{label}  [{ts_uid.get(label, '')}]  - {n} of {len(good)} "
                f"file(s)"))
        sop_counts: Counter = Counter(
            r.get("SOPClassUID", "") for r in good if r.get("SOPClassUID"))
        for uid, n in sorted(sop_counts.items(), key=lambda x: -x[1]):
            findings.append(Finding(
                "transfer", f"SOP class: {_uid_name(uid)}  ({n} file(s))",
                f"{_uid_name(uid)}  [{uid}]  - {n} of {len(good)} file(s)"))

    # C-STORE send-ability: round-trip each file through a built-in standard
    # archive to catch transfer-syntax / SOP-class problems a file-level check
    # misses.
    if sendability and good and not (should_cancel and should_cancel()):
        from ..net.selftest import store_selftest
        st = store_selftest([r["__file"] for r in good], on_item=on_item,
                            should_cancel=should_cancel, workers=workers)
        for r in good:
            ok, code = st.get(str(r["__file"]), (True, "ok"))
            if not ok:
                findings.append(Finding(
                    "transfer", f"{Path(r['__file']).name}: not send-able",
                    f"C-STORE to a standard archive failed: {code}"[:500]))

    return QAResult(files_read=len(good), findings=findings)
