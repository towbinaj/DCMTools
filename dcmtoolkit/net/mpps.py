"""Modality Performed Procedure Step (MPPS) SCU - N-CREATE / N-SET.

After a modality starts an exam it reports progress to the RIS/PACS as an MPPS:
an N-CREATE opens the step as ``IN PROGRESS`` (referencing the scheduled step
from the worklist), and an N-SET later closes it as ``COMPLETED`` (or
``DISCONTINUED``) with the series/instances actually acquired.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom import AE
from pynetdicom.sop_class import ModalityPerformedProcedureStep

from ..logging_setup import get_logger
from ..model import Node

log = get_logger("mpps")

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _now() -> tuple[str, str]:
    n = datetime.now()
    return n.strftime("%Y%m%d"), n.strftime("%H%M%S")


def build_create(item: dict, *, performed_station_ae: str, modality: str,
                 pps_id: str = "", pps_description: str = "",
                 start_date: str = "", start_time: str = "") -> tuple[Dataset, str]:
    """Build an MPPS N-CREATE dataset (status IN PROGRESS) from a worklist item.

    ``item`` is a flattened worklist dict (see :func:`mwl.flatten_item`). Returns
    ``(dataset, sop_instance_uid)`` - the SCU assigns the new SOP Instance UID.
    """
    d0, t0 = _now()
    start_date = start_date or d0
    start_time = start_time or t0
    sop_uid = generate_uid()

    ds = Dataset()
    # Patient
    ds.PatientName = item.get("PatientName", "")
    ds.PatientID = item.get("PatientID", "")
    ds.PatientBirthDate = item.get("PatientBirthDate", "")
    ds.PatientSex = item.get("PatientSex", "")

    # Scheduled step reference (ties the MPPS back to the worklist entry)
    sched = Dataset()
    sched.StudyInstanceUID = item.get("StudyInstanceUID", "") or generate_uid()
    sched.AccessionNumber = item.get("AccessionNumber", "")
    sched.RequestedProcedureID = item.get("RequestedProcedureID", "")
    sched.RequestedProcedureDescription = item.get(
        "RequestedProcedureDescription", "")
    sched.ScheduledProcedureStepID = item.get("ScheduledProcedureStepID", "")
    sched.ScheduledProcedureStepDescription = item.get(
        "ScheduledProcedureStepDescription", "")
    sched.ReferencedStudySequence = []
    ds.ScheduledStepAttributesSequence = [sched]

    # Performed step
    ds.PerformedStationAETitle = performed_station_ae
    ds.PerformedStationName = ""
    ds.PerformedLocation = ""
    ds.PerformedProcedureStepStartDate = start_date
    ds.PerformedProcedureStepStartTime = start_time
    ds.PerformedProcedureStepEndDate = ""
    ds.PerformedProcedureStepEndTime = ""
    ds.PerformedProcedureStepStatus = "IN PROGRESS"
    ds.PerformedProcedureStepID = pps_id or ("PPS" + start_time)
    ds.PerformedProcedureStepDescription = pps_description or \
        item.get("RequestedProcedureDescription", "")
    ds.PerformedProcedureTypeDescription = ""
    ds.Modality = modality
    ds.StudyID = ""
    ds.ProcedureCodeSequence = []
    ds.PerformedProtocolCodeSequence = []
    # Empty at IN PROGRESS; filled in on completion.
    ds.PerformedSeriesSequence = []
    return ds, sop_uid


def build_set(status: str, series: list[dict] | None = None, *,
              end_date: str = "", end_time: str = "") -> Dataset:
    """Build an MPPS N-SET modification list closing the step.

    ``status`` is ``COMPLETED`` or ``DISCONTINUED``. ``series`` is a list of
    dicts: ``{"SeriesInstanceUID", "SeriesDescription", "Modality",
    "ProtocolName", "RetrieveAETitle", "refs": [(sop_class, sop_instance), ...]}``.
    """
    d1, t1 = _now()
    ds = Dataset()
    ds.PerformedProcedureStepStatus = status
    ds.PerformedProcedureStepEndDate = end_date or d1
    ds.PerformedProcedureStepEndTime = end_time or t1

    seq = []
    for s in series or []:
        item = Dataset()
        item.SeriesInstanceUID = s.get("SeriesInstanceUID", "") or generate_uid()
        item.SeriesDescription = s.get("SeriesDescription", "")
        item.PerformingPhysicianName = s.get("PerformingPhysicianName", "")
        item.ProtocolName = s.get("ProtocolName", "")
        item.OperatorsName = s.get("OperatorsName", "")
        item.RetrieveAETitle = s.get("RetrieveAETitle", "")
        refs = []
        for sop_class, sop_inst in s.get("refs", []):
            r = Dataset()
            r.ReferencedSOPClassUID = sop_class
            r.ReferencedSOPInstanceUID = sop_inst
            refs.append(r)
        item.ReferencedImageSequence = refs
        seq.append(item)
    ds.PerformedSeriesSequence = seq
    return ds


def _associate(my_aetitle: str, node: Node, timeout: int, tls_args):
    ae = AE(ae_title=node.calling(my_aetitle))
    ae.add_requested_context(ModalityPerformedProcedureStep)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout
    return ae.associate(node.host, node.port, ae_title=node.aetitle,
                        tls_args=tls_args)


def mpps_create(my_aetitle: str, node: Node, ds: Dataset, sop_instance_uid: str,
                timeout: int = 30, progress: Progress = _noop,
                tls_args=None) -> tuple[bool, str]:
    """Send the MPPS N-CREATE. Returns ``(ok, detail)``."""
    progress(f"MPPS N-CREATE (IN PROGRESS) to {node.aetitle} ...")
    assoc = _associate(my_aetitle, node, timeout, tls_args)
    if not assoc.is_established:
        return False, "Association rejected / aborted / failed."
    try:
        status, _rsp = assoc.send_n_create(
            ds, ModalityPerformedProcedureStep, sop_instance_uid)
        if status and status.Status == 0x0000:
            return True, "MPPS created (IN PROGRESS)."
        code = f"0x{status.Status:04x}" if status else "no response"
        return False, f"N-CREATE failed ({code})."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).strip()[:200] or "error"
    finally:
        assoc.release()


def mpps_set(my_aetitle: str, node: Node, ds: Dataset, sop_instance_uid: str,
             timeout: int = 30, progress: Progress = _noop,
             tls_args=None) -> tuple[bool, str]:
    """Send the MPPS N-SET (COMPLETED/DISCONTINUED). Returns ``(ok, detail)``."""
    stat = str(getattr(ds, "PerformedProcedureStepStatus", "?"))
    progress(f"MPPS N-SET ({stat}) to {node.aetitle} ...")
    assoc = _associate(my_aetitle, node, timeout, tls_args)
    if not assoc.is_established:
        return False, "Association rejected / aborted / failed."
    try:
        status, _rsp = assoc.send_n_set(
            ds, ModalityPerformedProcedureStep, sop_instance_uid)
        if status and status.Status == 0x0000:
            return True, f"MPPS set {stat}."
        code = f"0x{status.Status:04x}" if status else "no response"
        return False, f"N-SET failed ({code})."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).strip()[:200] or "error"
    finally:
        assoc.release()
