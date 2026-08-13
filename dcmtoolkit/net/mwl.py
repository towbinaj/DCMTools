"""Modality Worklist (MWL) SCU - C-FIND against a worklist server (RIS).

A modality queries the worklist to discover the procedures scheduled for it:
who to scan, the accession/procedure, and the study identifiers to stamp onto
the acquired images. The query is a C-FIND on the Modality Worklist Information
Model; matching keys live both at the top level (patient/study) and inside the
Scheduled Procedure Step Sequence (station AE, modality, date).
"""

from __future__ import annotations

from typing import Callable

from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind

from ..logging_setup import get_logger
from ..model import Node
from .scu import FindResult

log = get_logger("mwl")

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# Scheduled Procedure Step Sequence return/match keys.
_SPS_KEYS = [
    "Modality", "ScheduledStationAETitle", "ScheduledProcedureStepStartDate",
    "ScheduledProcedureStepStartTime", "ScheduledPerformingPhysicianName",
    "ScheduledProcedureStepDescription", "ScheduledProcedureStepID",
    "ScheduledStationName", "ScheduledProcedureStepLocation",
]
# Top-level (patient + requested-procedure) return keys.
_TOP_KEYS = [
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
    "AccessionNumber", "StudyInstanceUID", "ReferringPhysicianName",
    "RequestedProcedureID", "RequestedProcedureDescription",
    "RequestedProcedurePriority", "AdmissionID",
]


def build_worklist_query(*, sps_start_date: str = "", sps_station_ae: str = "",
                         modality: str = "", patient_id: str = "",
                         patient_name: str = "", accession: str = "") -> Dataset:
    """Build a Modality Worklist C-FIND identifier.

    Empty string on a key means "return this attribute" (universal match);
    a value narrows the query. Date accepts a single ``YYYYMMDD`` or a range
    ``YYYYMMDD-YYYYMMDD`` per the DICOM date-range syntax.
    """
    ds = Dataset()
    for k in _TOP_KEYS:
        setattr(ds, k, "")
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.AccessionNumber = accession

    sps = Dataset()
    for k in _SPS_KEYS:
        setattr(sps, k, "")
    sps.Modality = modality
    sps.ScheduledStationAETitle = sps_station_ae
    sps.ScheduledProcedureStepStartDate = sps_start_date
    ds.ScheduledProcedureStepSequence = [sps]
    return ds


def query_worklist(my_aetitle: str, node: Node, identifier: Dataset,
                   timeout: int = 60, progress: Progress = _noop,
                   tls_args=None) -> FindResult:
    """Run the worklist C-FIND and return the matched scheduled steps."""
    result = FindResult()

    ae = AE(ae_title=node.calling(my_aetitle))
    ae.add_requested_context(ModalityWorklistInformationFind)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout

    scheme = "TLS " if tls_args else ""
    progress(f"Associating ({scheme}) with {node.aetitle}@{node.host}:"
             f"{node.port} for worklist ...")
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
    if not assoc.is_established:
        result.message = "Association rejected / aborted / failed."
        return result
    try:
        for status, ds in assoc.send_c_find(identifier,
                                            ModalityWorklistInformationFind):
            if status and status.Status in (0xFF00, 0xFF01) and ds:
                result.datasets.append(ds)
            elif status and status.Status == 0x0000:
                progress(f"Worklist C-FIND complete: "
                         f"{len(result.datasets)} item(s).")
            elif status:
                progress(f"Worklist C-FIND status 0x{status.Status:04x}")
        result.message = f"{len(result.datasets)} worklist item(s)."
    finally:
        assoc.release()
    return result


def flatten_item(ds: Dataset) -> dict:
    """Flatten a worklist result (top level + first SPS item) into a dict."""
    out: dict[str, str] = {}
    for k in _TOP_KEYS:
        out[k] = str(getattr(ds, k, "") or "")
    sps_seq = getattr(ds, "ScheduledProcedureStepSequence", None) or []
    sps = sps_seq[0] if sps_seq else None
    for k in _SPS_KEYS:
        out[k] = str(getattr(sps, k, "") or "") if sps is not None else ""
    return out
