"""Compute the on-disk path for a received object.

Mirrors the legacy DCMStoreService ``SaveFolderFormat`` options:
UID, FLAT, MINT, MEDIS, PATIENT, STUDY. Illegal filesystem characters in any
DICOM-derived path component are replaced with an underscore.
"""

from __future__ import annotations

from pathlib import Path

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from ..tools.fileops import sanitize

FORMATS = ["UID", "FLAT", "MINT", "MEDIS", "PATIENT", "STUDY"]


def _g(ds: Dataset, attr: str, default: str = "") -> str:
    return sanitize(getattr(ds, attr, default) or default)


def relative_path(ds: Dataset, fmt: str) -> Path:
    fmt = (fmt or "UID").upper()
    study_uid = _g(ds, "StudyInstanceUID", "NOSTUDY")
    series_uid = _g(ds, "SeriesInstanceUID", "NOSERIES")
    sop_uid = _g(ds, "SOPInstanceUID") or sanitize(str(generate_uid()))
    unique = sop_uid[-16:] if sop_uid else sanitize(str(generate_uid()))

    if fmt == "UID":
        return Path(study_uid) / series_uid / f"{sop_uid}.dcm"

    if fmt == "FLAT":
        return Path(study_uid) / f"{sop_uid}.dcm"

    if fmt == "MINT":
        return Path(f"newstudy {study_uid}") / f"{sop_uid}.dcm"

    if fmt == "MEDIS":
        folder = (f"{_g(ds, 'PatientName', 'NONAME')}_"
                  f"{_g(ds, 'StudyDate', 'NODATE')}_"
                  f"{_g(ds, 'PatientID', 'NOID')}")
        return Path(folder) / f"{_g(ds, 'Modality', 'XX')}.X.{sop_uid}.dcm"

    if fmt == "PATIENT":
        pt = f"PT-{_g(ds, 'PatientID', 'NOID')}-{_g(ds, 'PatientName', 'NONAME')}"
        st = (f"ST-{_g(ds, 'StudyDate', 'NODATE')}-"
              f"{_g(ds, 'AccessionNumber', 'NOACC')}-"
              f"{_g(ds, 'StudyDescription', 'NODESC')}")
        se = (f"SE-{_g(ds, 'SeriesNumber', '0')}-"
              f"{_g(ds, 'SeriesDescription', 'NODESC')}")
        im = (f"IM-{_g(ds, 'Modality', 'XX')}-"
              f"{_g(ds, 'InstanceNumber', '0')}-{unique}.dcm")
        return Path(pt) / st / se / im

    if fmt == "STUDY":
        st = (f"{_g(ds, 'PatientID', 'NOID')}-"
              f"{_g(ds, 'StudyDate', 'NODATE')}-"
              f"{_g(ds, 'AccessionNumber', 'NOACC')}-"
              f"{_g(ds, 'StudyDescription', 'NODESC')}")
        se = (f"SE-{_g(ds, 'SeriesNumber', '0')}-"
              f"{_g(ds, 'SeriesDescription', 'NODESC')}")
        im = (f"IM-{_g(ds, 'Modality', 'XX')}-"
              f"{_g(ds, 'InstanceNumber', '0')}-{unique}.dcm")
        return Path(st) / se / im

    # Fallback to UID layout for unknown formats.
    return Path(study_uid) / series_uid / f"{sop_uid}.dcm"
