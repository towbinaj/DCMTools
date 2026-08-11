"""DICOM SCU operations: C-ECHO, C-STORE, C-FIND, C-MOVE.

Each function takes the local AE title, a target :class:`~dcmtoolkit.model.Node`,
and an optional ``progress`` callback ``(message: str) -> None`` used to stream
status back into the GUI. They are synchronous and intended to be run on a
worker thread by the UI layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom import AE, evt
from pynetdicom.presentation import (
    StoragePresentationContexts,
    VerificationPresentationContexts,
    build_context,
)
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from ..logging_setup import get_logger
from ..model import Node

log = get_logger("scu")

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class EchoResult:
    success: bool
    message: str


@dataclass
class StoreResult:
    sent: int = 0
    failed: int = 0
    warnings: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.failed


@dataclass
class FindResult:
    datasets: list[Dataset] = field(default_factory=list)
    message: str = ""


@dataclass
class MoveResult:
    completed: int = 0
    failed: int = 0
    warning: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# C-ECHO
# ---------------------------------------------------------------------------
def c_echo(my_aetitle: str, node: Node, timeout: int = 30,
           progress: Progress = _noop) -> EchoResult:
    ae = AE(ae_title=my_aetitle)
    ae.requested_contexts = VerificationPresentationContexts
    ae.acse_timeout = timeout
    ae.dimse_timeout = timeout
    ae.network_timeout = timeout

    progress(f"Associating with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle)
    if not assoc.is_established:
        return EchoResult(False, "Association rejected / aborted / failed.")
    try:
        status = assoc.send_c_echo()
        if status and status.Status == 0x0000:
            progress("C-ECHO success (0x0000).")
            return EchoResult(True, "Success - remote AE responded to C-ECHO.")
        code = f"0x{status.Status:04x}" if status else "no response"
        return EchoResult(False, f"C-ECHO returned status {code}.")
    finally:
        assoc.release()


# ---------------------------------------------------------------------------
# C-STORE
# ---------------------------------------------------------------------------
def _contexts_for_files(files: list[Path]) -> list:
    """Build the minimal set of presentation contexts for the given files."""
    contexts: dict[tuple[str, str], object] = {}
    for f in files:
        try:
            ds = dcmread(str(f), stop_before_pixels=True, force=True)
            sop = ds.file_meta.MediaStorageSOPClassUID
            ts = ds.file_meta.TransferSyntaxUID
        except Exception:  # noqa: BLE001 - unreadable file handled at send time
            continue
        key = (str(sop), str(ts))
        if key not in contexts:
            contexts[key] = build_context(sop, ts)
    return list(contexts.values())


def c_store(my_aetitle: str, node: Node, files: Iterable[Path],
            progress: Progress = _noop) -> StoreResult:
    files = [Path(f) for f in files]
    result = StoreResult()
    if not files:
        result.errors.append("No files selected.")
        return result

    contexts = _contexts_for_files(files)
    if not contexts:
        result.errors.append("None of the selected files are readable DICOM.")
        result.failed = len(files)
        return result

    ae = AE(ae_title=my_aetitle)
    # A single association supports at most 128 presentation contexts.
    ae.requested_contexts = contexts[:128]

    progress(f"Associating with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle)
    if not assoc.is_established:
        result.errors.append("Association rejected / aborted / failed.")
        result.failed = len(files)
        return result

    try:
        for i, f in enumerate(files, 1):
            try:
                ds = dcmread(str(f), force=True)
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{f.name}: unreadable ({exc})")
                continue
            status = assoc.send_c_store(ds)
            if status and status.Status == 0x0000:
                result.sent += 1
                progress(f"[{i}/{len(files)}] Sent {f.name}")
            elif status and status.Status in (0xB000, 0xB006, 0xB007):
                result.sent += 1
                result.warnings += 1
                progress(f"[{i}/{len(files)}] Sent {f.name} (warning "
                         f"0x{status.Status:04x})")
            else:
                result.failed += 1
                code = f"0x{status.Status:04x}" if status else "no response"
                result.errors.append(f"{f.name}: C-STORE status {code}")
                progress(f"[{i}/{len(files)}] FAILED {f.name} ({code})")
    finally:
        assoc.release()

    progress(f"Done. Sent {result.sent}, failed {result.failed}.")
    return result


# ---------------------------------------------------------------------------
# C-FIND
# ---------------------------------------------------------------------------
_FIND_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelFind,
    "STUDY": StudyRootQueryRetrieveInformationModelFind,
}
_MOVE_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelMove,
    "STUDY": StudyRootQueryRetrieveInformationModelMove,
}


def build_query(level: str, **criteria: str) -> Dataset:
    """Create a C-FIND/C-MOVE identifier dataset.

    ``level`` is one of PATIENT/STUDY/SERIES/IMAGE. ``criteria`` are DICOM
    keyword=value pairs (empty string means "return this attribute").
    """
    ds = Dataset()
    ds.QueryRetrieveLevel = level
    for key, value in criteria.items():
        setattr(ds, key, value)
    return ds


def c_find(my_aetitle: str, node: Node, identifier: Dataset,
           model: str = "STUDY", timeout: int = 60,
           progress: Progress = _noop) -> FindResult:
    result = FindResult()
    find_model = _FIND_MODELS[model.upper()]

    ae = AE(ae_title=my_aetitle)
    ae.add_requested_context(find_model)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    progress(f"Associating with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle)
    if not assoc.is_established:
        result.message = "Association rejected / aborted / failed."
        return result
    try:
        responses = assoc.send_c_find(identifier, find_model)
        for status, ds in responses:
            if status and status.Status in (0xFF00, 0xFF01) and ds:
                result.datasets.append(ds)
            elif status and status.Status == 0x0000:
                progress(f"C-FIND complete: {len(result.datasets)} match(es).")
            elif status:
                progress(f"C-FIND status 0x{status.Status:04x}")
        result.message = f"{len(result.datasets)} match(es)."
    finally:
        assoc.release()
    return result


# ---------------------------------------------------------------------------
# C-MOVE
# ---------------------------------------------------------------------------
def c_move(my_aetitle: str, node: Node, dest_aetitle: str,
           identifier: Dataset, model: str = "STUDY", timeout: int = 120,
           progress: Progress = _noop) -> MoveResult:
    result = MoveResult()
    move_model = _MOVE_MODELS[model.upper()]

    ae = AE(ae_title=my_aetitle)
    ae.add_requested_context(move_model)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    progress(f"Requesting move from {node.aetitle} -> {dest_aetitle} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle)
    if not assoc.is_established:
        result.message = "Association rejected / aborted / failed."
        return result
    try:
        responses = assoc.send_c_move(identifier, dest_aetitle, move_model)
        for status, _ds in responses:
            if not status:
                continue
            if "NumberOfCompletedSuboperations" in status:
                result.completed = status.NumberOfCompletedSuboperations or 0
            if "NumberOfFailedSuboperations" in status:
                result.failed = status.NumberOfFailedSuboperations or 0
            if "NumberOfWarningSuboperations" in status:
                result.warning = status.NumberOfWarningSuboperations or 0
            progress(f"Move progress: {result.completed} done, "
                     f"{result.failed} failed, {result.warning} warn")
        result.message = (f"Completed {result.completed}, failed "
                          f"{result.failed}, warnings {result.warning}.")
    finally:
        assoc.release()
    return result
