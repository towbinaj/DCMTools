"""IOD conformance validation (dicom-validator), fully offline.

pydicom only validates individual values (VRs, basic presence). This module
adds IOD/module conformance: for a dataset's SOP Class, does it carry the
required modules and the Type-1/1C/2/2C attributes within them, per the DICOM
standard. It uses the ``dicom-validator`` package against a copy of the DICOM
standard (parsed to JSON) bundled with the app - so no network access is ever
needed and no data leaves the machine.
"""

from __future__ import annotations

import logging
import threading

from pydicom.datadict import keyword_for_tag
from pydicom.dataset import Dataset

from .. import paths
from ..logging_setup import get_logger

log = get_logger("conformance")

_lock = threading.Lock()
_loaded = False
_dicom_info = None          # cached DicomInfo, or None if unavailable
_edition = ""
_unavailable_reason = ""


def _standard_base():
    return paths.resource_dir() / "assets" / "dicom-standard"


def _load():
    """Load the bundled standard once (thread-safe). Never raises."""
    global _loaded, _dicom_info, _edition, _unavailable_reason
    with _lock:
        if _loaded:
            return
        _loaded = True
        base = _standard_base()
        try:
            editions = sorted(d.name for d in base.iterdir()
                              if (d / "json").is_dir())
        except OSError:
            editions = []
        if not editions:
            _unavailable_reason = (f"DICOM standard not found at {base}")
            return
        edition = editions[-1]  # newest bundled edition
        try:
            from dicom_validator.spec_reader.edition_reader import EditionReader
            _dicom_info = EditionReader(str(base)).load_dicom_info(edition)
            _edition = edition
            log.info("Loaded DICOM standard edition %s for conformance", edition)
        except Exception as exc:  # noqa: BLE001
            _unavailable_reason = f"Could not load standard: {exc}"
            _dicom_info = None


def available() -> bool:
    """True if conformance validation can run (package + bundled standard)."""
    _load()
    return _dicom_info is not None


def edition() -> str:
    _load()
    return _edition


def unavailable_reason() -> str:
    _load()
    return _unavailable_reason


def _name(x) -> str:
    return getattr(x, "name", str(x))


def conformance_errors(ds: Dataset) -> list[str]:
    """Return human-readable IOD conformance problems for ``ds`` (empty = OK).

    Never raises - on any internal failure returns a single explanatory note so
    a batch scan is never aborted by one odd file.
    """
    _load()
    if _dicom_info is None:
        return []
    try:
        from dicom_validator.validator.iod_validator import IODValidator
        from dicom_validator.validator.validation_result import Status
        result = IODValidator(ds, _dicom_info, log_level=logging.ERROR).validate()
    except Exception as exc:  # noqa: BLE001
        return [f"validator error: {str(exc).strip()[:120]}"]

    if result.status == Status.UnknownSOPClassUID:
        return [f"unknown/unsupported SOP Class {result.sop_class_uid} "
                "(not in standard - cannot check IOD)"]
    if result.status in (Status.MissingSOPClassUID, Status.InvalidFile):
        return [f"not a validatable DICOM object ({_name(result.status)})"]

    out: list[str] = []
    for module, tag_errors in result.module_errors.items():
        for dtag, terr in tag_errors.items():
            kw = keyword_for_tag(dtag.tag) or ""
            out.append(f"{module} / {dtag} {kw}: {_name(terr.code)} "
                       f"(Type {terr.type})")
    return out
