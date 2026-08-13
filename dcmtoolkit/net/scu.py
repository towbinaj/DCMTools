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
from pynetdicom import AE, evt, build_role
from pynetdicom.presentation import (
    StoragePresentationContexts,
    VerificationPresentationContexts,
    build_context,
)
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    PatientRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelGet,
    StorageCommitmentPushModel,
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
           progress: Progress = _noop, tls_args=None) -> EchoResult:
    ae = AE(ae_title=my_aetitle)
    ae.requested_contexts = VerificationPresentationContexts
    ae.acse_timeout = timeout
    ae.dimse_timeout = timeout
    ae.network_timeout = timeout

    scheme = "TLS " if tls_args else ""
    progress(f"Associating ({scheme}) with {node.aetitle}@{node.host}:"
             f"{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
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
def _contexts_for_files(files: list[Path], progress: Progress = _noop) -> list:
    """Build presentation contexts for the exact SOP classes + transfer syntaxes
    present in the files.

    Proposing precisely what the files contain guarantees the peer can negotiate
    a matching context (unlike a fixed "all storage" list, which some archives
    only partially accept). Capped at the 128-context association limit.
    """
    from pydicom.uid import ExplicitVRLittleEndian
    contexts: dict[tuple[str, str], object] = {}
    total = len(files)
    for i, f in enumerate(files, 1):
        sop = ts = None
        try:
            ds = dcmread(str(f), stop_before_pixels=True, force=True)
            fm = getattr(ds, "file_meta", None)
            if fm is not None:
                sop = getattr(fm, "MediaStorageSOPClassUID", None)
                ts = getattr(fm, "TransferSyntaxUID", None)
            if not sop:
                sop = getattr(ds, "SOPClassUID", None)
            if not ts:
                ts = ExplicitVRLittleEndian
        except Exception:  # noqa: BLE001 - unreadable handled at send time
            continue
        if not sop:
            continue
        key = (str(sop), str(ts))
        if key not in contexts and len(contexts) < 128:
            contexts[key] = build_context(sop, ts)
        if total > 3000 and i % 3000 == 0:
            progress(f"Preparing: scanned {i:,}/{total:,} files "
                     f"({len(contexts)} context(s))...")
    return list(contexts.values())


OnFile = Callable[[int, int, Path, bool, str], None]


def c_store(my_aetitle: str, node: Node, files: Iterable[Path],
            progress: Progress = _noop,
            should_cancel: Callable[[], bool] | None = None,
            on_frac: Callable[[float], None] | None = None,
            timeout: int = 30, tls_args=None,
            on_file: OnFile | None = None, verbose: bool = False) -> StoreResult:
    """Send DICOM files via C-STORE over one association.

    ``on_file(index, total, path, ok, code)`` is invoked once per file with
    lightweight data; the UI reads it to render throttled live progress instead
    of logging every file. ``verbose`` additionally emits a per-file log line.
    """
    files = [Path(f) for f in files]
    result = StoreResult()
    total = len(files)
    if not files:
        result.errors.append("No files selected.")
        return result

    progress(f"Preparing to send {total:,} file(s)...")
    contexts = _contexts_for_files(files, progress)
    if not contexts:
        msg = "No readable DICOM presentation contexts in the selection."
        result.errors.append(msg)
        for i, f in enumerate(files, 1):
            if on_file:
                on_file(i, total, f, False, "not DICOM")
        result.failed = total
        return result

    ae = AE(ae_title=my_aetitle)
    ae.requested_contexts = contexts
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    scheme = "TLS " if tls_args else ""
    progress(f"Proposing {len(contexts)} context(s). Associating ({scheme})"
             f" with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
    if not assoc.is_established:
        result.errors.append("Association rejected / aborted / failed.")
        result.failed = total
        progress("Association rejected - the peer refused the connection or "
                 "the proposed presentation contexts.")
        for i, f in enumerate(files, 1):
            if on_file:
                on_file(i, total, f, False, "association rejected")
        return result
    # Warn if the peer accepted the association but no storage contexts.
    if not assoc.accepted_contexts:
        progress("Warning: peer accepted the association but NO presentation "
                 "contexts - all sends will fail.")
    progress(f"Association established ({len(assoc.accepted_contexts)} "
             f"accepted context(s)). Sending {total:,} file(s)...")

    def record(i, f, ok, code):
        if on_file:
            on_file(i, total, f, ok, code)

    try:
        for i, f in enumerate(files, 1):
            if should_cancel is not None and should_cancel():
                progress(f"Cancelled after {i - 1} of {total:,} file(s).")
                break

            if not assoc.is_established:
                result.errors.append("Association was lost mid-transfer.")
                progress("Association lost - stopping.")
                result.failed += (total - i + 1)
                break

            ok, code = False, "error"
            try:
                ds = dcmread(str(f), force=True)
                if "SOPClassUID" not in ds or "SOPInstanceUID" not in ds:
                    raise ValueError("not a DICOM object (missing SOP UID)")
                status = assoc.send_c_store(ds)
                if status and status.Status == 0x0000:
                    ok, code = True, "0x0000"
                    result.sent += 1
                elif status and status.Status in (0xB000, 0xB006, 0xB007):
                    ok, code = True, f"0x{status.Status:04x}"
                    result.sent += 1
                    result.warnings += 1
                else:
                    code = f"0x{status.Status:04x}" if status else "no-response"
                    result.failed += 1
                    if len(result.errors) < 5000:
                        result.errors.append(f"{f}: C-STORE {code}")
            except Exception as exc:  # noqa: BLE001 - never let one file abort
                code = str(exc).strip().replace("\n", " ")[:200] or "error"
                result.failed += 1
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {exc}")

            record(i, f, ok, code)
            if verbose:
                tag = "Sent" if ok else "FAILED"
                progress(f"[{i}/{total}] {tag} {f.name} ({code})")
            if on_frac:
                on_frac(i / total)
    finally:
        assoc.release()

    progress(f"Done. Sent {result.sent:,}, failed {result.failed:,}.")
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
           progress: Progress = _noop, tls_args=None) -> FindResult:
    result = FindResult()
    find_model = _FIND_MODELS[model.upper()]

    ae = AE(ae_title=my_aetitle)
    ae.add_requested_context(find_model)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    progress(f"Associating with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
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
           progress: Progress = _noop, tls_args=None) -> MoveResult:
    result = MoveResult()
    move_model = _MOVE_MODELS[model.upper()]

    ae = AE(ae_title=my_aetitle)
    ae.add_requested_context(move_model)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    progress(f"Requesting move from {node.aetitle} -> {dest_aetitle} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
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


# ---------------------------------------------------------------------------
# C-GET (retrieve over the same association)
# ---------------------------------------------------------------------------
_GET_MODELS = {
    "PATIENT": PatientRootQueryRetrieveInformationModelGet,
    "STUDY": StudyRootQueryRetrieveInformationModelGet,
}


@dataclass
class GetResult:
    completed: int = 0
    failed: int = 0
    warning: int = 0
    saved: int = 0
    message: str = ""
    saved_files: list = field(default_factory=list)


def c_get(my_aetitle: str, node: Node, identifier: Dataset, out_dir: Path,
          model: str = "STUDY", timeout: int = 300,
          progress: Progress = _noop, tls_args=None,
          on_object=None) -> GetResult:
    """Retrieve objects with C-GET, saving them locally over one association.

    Unlike C-MOVE, C-GET streams the objects back on the same connection, so no
    separate destination AE needs to be registered on the remote node.
    ``on_object(count, total_remaining)`` is called after each object is saved.
    """
    result = GetResult()
    get_model = _GET_MODELS[model.upper()]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = {"n": 0, "remaining": 0}

    def handle_store(event):
        ds = event.dataset
        ds.file_meta = event.file_meta
        uid = getattr(ds, "SOPInstanceUID", None) or generate_uid()
        dest = out_dir / f"{uid}.dcm"
        dest.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(str(dest), enforce_file_format=True)
        saved["n"] += 1
        if len(result.saved_files) < 100000:
            result.saved_files.append(str(dest))
        if on_object:
            on_object(saved["n"], saved["remaining"])
        return 0x0000

    ae = AE(ae_title=my_aetitle)
    ae.add_requested_context(get_model)
    # Propose storage contexts with the SCP role so the remote can send objects
    # back to us. Cap to stay within the 128-context association limit.
    roles = []
    for cx in StoragePresentationContexts[:115]:
        ae.add_requested_context(cx.abstract_syntax)
        roles.append(build_role(cx.abstract_syntax, scp_role=True))
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    progress(f"Associating with {node.aetitle}@{node.host}:{node.port} ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         ext_neg=roles, tls_args=tls_args,
                         evt_handlers=[(evt.EVT_C_STORE, handle_store)])
    if not assoc.is_established:
        result.message = "Association rejected / aborted / failed."
        return result
    try:
        for status, _ds in assoc.send_c_get(identifier, get_model):
            if not status:
                continue
            if "NumberOfCompletedSuboperations" in status:
                result.completed = status.NumberOfCompletedSuboperations or 0
            if "NumberOfFailedSuboperations" in status:
                result.failed = status.NumberOfFailedSuboperations or 0
            if "NumberOfWarningSuboperations" in status:
                result.warning = status.NumberOfWarningSuboperations or 0
            if "NumberOfRemainingSuboperations" in status:
                saved["remaining"] = status.NumberOfRemainingSuboperations or 0
        result.saved = saved["n"]
        result.message = (f"Saved {result.saved} object(s) "
                          f"(completed {result.completed}, "
                          f"failed {result.failed}).")
    finally:
        assoc.release()
    return result


# ---------------------------------------------------------------------------
# Storage Commitment (N-ACTION request + N-EVENT-REPORT result)
# ---------------------------------------------------------------------------
@dataclass
class CommitResult:
    requested: int = 0
    committed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    report_received: bool = False
    message: str = ""


def storage_commit(my_aetitle: str, node: Node, sop_refs: list[tuple[str, str]],
                   listen_port: int = 11115, timeout: int = 60,
                   progress: Progress = _noop) -> CommitResult:
    """Request storage commitment for the given (SOPClassUID, SOPInstanceUID).

    Opens a temporary local SCP to receive the asynchronous N-EVENT-REPORT the
    archive sends back, then issues the N-ACTION request. Waits up to ``timeout``
    seconds for the report.

    NOTE: the remote archive must be able to reach ``my_aetitle`` at
    ``listen_port`` for the result report to arrive.
    """
    import threading

    result = CommitResult(requested=len(sop_refs))
    txn_uid = generate_uid()
    done_evt = threading.Event()

    def handle_report(event):
        ds = event.event_information
        for item in getattr(ds, "ReferencedSOPSequence", []) or []:
            result.committed.append(str(item.ReferencedSOPInstanceUID))
        for item in getattr(ds, "FailedSOPSequence", []) or []:
            result.failed.append(str(item.ReferencedSOPInstanceUID))
        result.report_received = True
        done_evt.set()
        return 0x0000

    # Local SCP to catch the report.
    scp_ae = AE(ae_title=my_aetitle)
    scp_ae.add_supported_context(StorageCommitmentPushModel)
    server = scp_ae.start_server(
        ("0.0.0.0", listen_port), block=False,
        evt_handlers=[(evt.EVT_N_EVENT_REPORT, handle_report)])

    try:
        # Build the N-ACTION request dataset.
        req = Dataset()
        req.TransactionUID = txn_uid
        req.ReferencedSOPSequence = []
        for sop_class, sop_inst in sop_refs:
            item = Dataset()
            item.ReferencedSOPClassUID = sop_class
            item.ReferencedSOPInstanceUID = sop_inst
            req.ReferencedSOPSequence.append(item)

        ae = AE(ae_title=my_aetitle)
        ae.add_requested_context(StorageCommitmentPushModel)
        ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout
        progress(f"Requesting commitment for {len(sop_refs)} instance(s)...")
        assoc = ae.associate(node.host, node.port, ae_title=node.aetitle)
        if not assoc.is_established:
            result.message = "Association rejected / aborted / failed."
            return result
        try:
            status, _reply = assoc.send_n_action(
                req, 1, StorageCommitmentPushModel,
                "1.2.840.10008.1.20.1.1")  # well-known SOP instance
        finally:
            assoc.release()

        if not status or status.Status != 0x0000:
            code = f"0x{status.Status:04x}" if status else "no response"
            result.message = f"N-ACTION failed ({code})."
            return result

        progress("N-ACTION accepted; waiting for result report...")
        if done_evt.wait(timeout):
            result.message = (f"Committed {len(result.committed)}, "
                              f"failed {len(result.failed)}.")
        else:
            result.message = ("No result report received within "
                              f"{timeout}s (archive may report later, or "
                              "cannot reach this AE/port).")
    finally:
        server.shutdown()
    return result
