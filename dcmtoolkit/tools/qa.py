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


def qa_scan(files, on_item=None, should_cancel=None, workers: int = 1) -> QAResult:
    files = [Path(f) for f in files]
    total = len(files)
    records: list[dict] = []
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
            rec = {"__file": f}
            for kw in _FIELDS:
                v = ds.get(kw)
                rec[kw] = "" if v is None else str(v)
            # DICOMDIR and other Media Storage Directory files are not image
            # instances - exclude them from the instance-level checks.
            fm = getattr(ds, "file_meta", None)
            msc = str(getattr(fm, "MediaStorageSOPClassUID", "") or "") \
                if fm is not None else ""
            if msc == "1.2.840.10008.1.3.10" or f.name.upper() == "DICOMDIR":
                rec["__skip"] = "DICOMDIR / media directory"
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

    return QAResult(files_read=len(good), findings=findings)
