"""Modality exam helper: stamp a folder of images with a worklist item's
identity, and collect the performed-series info needed to close the MPPS.

When a simulated modality "acquires" an exam, the images must carry the
patient/study identifiers from the scheduled procedure (so they file correctly
against the order). This module rewrites those identifiers, gives the exam a
fresh Study/Series/SOP UID lineage (so re-runs don't collide), and records the
series + SOP instances actually produced for the MPPS N-SET.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread
from pydicom.uid import generate_uid

from .fileops import _run_pool

# Worklist-item keys copied onto every image of the exam.
_PATIENT_KEYS = ["PatientName", "PatientID", "PatientBirthDate", "PatientSex"]


@dataclass
class StampResult:
    written: list[Path] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)  # for mpps.build_set
    study_uid: str = ""
    total: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def stamp_exam(files, item: dict, out_dir: Path, *, store_ae: str = "",
               new_uids: bool = True, on_item=None, should_cancel=None,
               workers: int = 1) -> StampResult:
    """Rewrite ``files`` to carry ``item``'s identity, writing to ``out_dir``.

    ``item`` is a flattened worklist dict (see :func:`mwl.flatten_item`).
    Returns a :class:`StampResult` including the ``series`` list to hand to
    :func:`mpps.build_set`.
    """
    files = [Path(f) for f in files]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(files)

    study_uid = item.get("StudyInstanceUID", "") or generate_uid()
    accession = item.get("AccessionNumber", "")
    study_desc = item.get("RequestedProcedureDescription", "")
    study_id = (item.get("RequestedProcedureID", "") or "")[:16]

    lock = threading.Lock()
    series_map: dict[str, str] = {}          # source series UID -> new UID
    series_info: dict[str, dict] = {}        # new series UID -> info + refs
    result = StampResult(study_uid=study_uid, total=total)
    counter = {"n": 0}
    stop = {"v": False}

    def process(f: Path):
        if stop["v"]:
            return
        if should_cancel is not None and should_cancel():
            stop["v"] = True
            return
        ok, detail = True, "stamped"
        try:
            ds = dcmread(str(f), force=True)
            if "SOPClassUID" not in ds:
                raise ValueError("not a DICOM image (no SOPClassUID)")

            for k in _PATIENT_KEYS:
                setattr(ds, k, item.get(k, "") or "")
            ds.AccessionNumber = accession
            ds.StudyInstanceUID = study_uid
            if study_desc:
                ds.StudyDescription = study_desc
            if study_id:
                ds.StudyID = study_id
            if item.get("ReferringPhysicianName"):
                ds.ReferringPhysicianName = item["ReferringPhysicianName"]

            src_series = str(getattr(ds, "SeriesInstanceUID", "") or "solo")
            with lock:
                new_series = series_map.get(src_series)
                if new_series is None:
                    new_series = generate_uid() if new_uids else src_series
                    series_map[src_series] = new_series
                    series_info[new_series] = {
                        "SeriesInstanceUID": new_series,
                        "SeriesDescription": str(
                            getattr(ds, "SeriesDescription", "") or ""),
                        "Modality": str(getattr(ds, "Modality", "") or ""),
                        "RetrieveAETitle": store_ae,
                        "refs": [],
                    }
            ds.SeriesInstanceUID = new_series

            sop_class = str(getattr(ds, "SOPClassUID", ""))
            sop_inst = generate_uid() if new_uids else \
                str(getattr(ds, "SOPInstanceUID", "") or generate_uid())
            ds.SOPInstanceUID = sop_inst
            fm = getattr(ds, "file_meta", None)
            if fm is not None:
                fm.MediaStorageSOPInstanceUID = sop_inst

            dest = out_dir / f"{sop_inst}.dcm"
            ds.save_as(str(dest), enforce_file_format=True)

            with lock:
                series_info[new_series]["refs"].append((sop_class, sop_inst))
                result.written.append(dest)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc).strip()[:200] or "error"
            with lock:
                result.failed += 1
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {detail}")
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if on_item:
            on_item(n, total, f, ok, detail)

    _run_pool(files, process, workers, stop)
    result.series = list(series_info.values())
    return result
