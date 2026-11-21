"""Worklist Morph engine: re-stamp a real study with a scheduled order's identity.

Builds test studies for a Test VNA/PACS: take a real study's pixels (downloaded by
C-GET from the Production VNA) and apply the identity of a Modality Worklist order
on top, then send it on. This module is the GUI-free engine; the network chain
(DMWL query -> C-FIND -> C-GET -> morph -> C-STORE -> cleanup) is orchestrated
elsewhere and calls :func:`morph_files`.

Two modes (set by the caller):

* **Scrub (default, recommended):** run the study through the de-identification
  engine first (PS3.15 Basic Profile + structural sanitize + the burned-in guard),
  which strips the *real* patient's PHI and consistently remaps Series/SOP UIDs,
  then stamp the order identity on top. No real PHI reaches the Test VNA.
* **Verbatim:** no scrub - only the order identity is stamped and fresh Series/SOP
  UIDs are minted (to avoid collisions on reuse). Use ONLY when the Test VNA is an
  approved PHI environment; the rest of the real study is carried through.

Per the design decisions: the order's Ordering Physician maps to RequestingPhysician
(0032,1032); Scheduled Station AE is NOT written into the object (no image-IOD home);
the order's Patient ID is applied for worklist correlation. The 128-byte preamble is
always zeroed on write (via ``save_dicom``) in both modes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from ..logging_setup import get_logger
from ..model import StudyContext
from ..store.processing import Processor, ReceiverConfig
from .fileops import save_dicom

log = get_logger("worklistmorph")
Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _s(obj, keyword: str) -> str:
    v = getattr(obj, keyword, "")
    return "" if v is None else str(v)


def order_to_context(item: Dataset) -> StudyContext:
    """Map one Modality Worklist item (a C-FIND-MWL result) to a StudyContext.

    Study Date comes from the Scheduled Procedure Step start date; the description
    from Requested Procedure Description (falling back to the SPS description).
    """
    sps = None
    seq = getattr(item, "ScheduledProcedureStepSequence", None)
    if seq and len(seq):
        sps = seq[0]
    study_date = _s(sps, "ScheduledProcedureStepStartDate") if sps is not None else ""
    desc = _s(item, "RequestedProcedureDescription")
    if not desc and sps is not None:
        desc = _s(sps, "ScheduledProcedureStepDescription")
    return StudyContext(
        patient_id=_s(item, "PatientID"),
        patient_name=_s(item, "PatientName"),
        birth_date=_s(item, "PatientBirthDate"),
        sex=_s(item, "PatientSex"),
        study_uid=_s(item, "StudyInstanceUID"),
        study_date=study_date,
        accession=_s(item, "AccessionNumber"),
        study_description=desc,
        referring_physician=_s(item, "ReferringPhysicianName"),
        requesting_physician=_s(item, "RequestingPhysician"),
    )


def _remap(uid: str, uid_map: dict) -> str:
    """Stable per-study UID remap: the same source UID always maps to the same new
    one, so a study keeps its series/instance structure under fresh identities."""
    if not uid:
        return uid
    if uid not in uid_map:
        uid_map[uid] = generate_uid()
    return uid_map[uid]


def _verbatim_remap(ds: Dataset, uid_map: dict) -> None:
    """Mint fresh Series/SOP/Frame-of-Reference UIDs (consistent across the study)
    without touching anything else. Study Instance UID is set from the order in
    :func:`_stamp_identity`, so it is not remapped here."""
    for attr in ("SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID"):
        v = getattr(ds, attr, None)
        if v:
            setattr(ds, attr, _remap(str(v), uid_map))
    fm = getattr(ds, "file_meta", None)
    if fm is not None and getattr(fm, "MediaStorageSOPInstanceUID", None):
        fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID


def _stamp_identity(ds: Dataset, ctx: StudyContext) -> None:
    """Overwrite the order's identity onto ``ds`` (ctx.study_uid must be resolved)."""
    ds.PatientName = ctx.patient_name or ""
    ds.PatientID = ctx.patient_id or ""
    ds.PatientBirthDate = ctx.birth_date or ""
    ds.PatientSex = ctx.sex or ""
    ds.AccessionNumber = ctx.accession or ""
    if ctx.study_date:
        ds.StudyDate = ctx.study_date
    if ctx.study_description:
        ds.StudyDescription = ctx.study_description
    if ctx.requesting_physician:
        ds.RequestingPhysician = ctx.requesting_physician   # (0032,1032)
    ds.StudyInstanceUID = ctx.study_uid


def morph_dataset(ds: Dataset, ctx: StudyContext, *,
                  scrub_cfg: ReceiverConfig | None = None,
                  uid_map: dict | None = None) -> Dataset:
    """Morph one object in place and return it.

    ``scrub_cfg`` set -> Scrub mode: the de-identification engine strips PHI,
    sanitizes carriers, enforces the burned-in guard and remaps UIDs; then the
    order identity is stamped. ``scrub_cfg`` None -> Verbatim mode: only fresh
    Series/SOP UIDs are minted (via ``uid_map``) and the identity stamped.

    ``ctx.study_uid`` must already be resolved (non-empty) so every object in a
    study shares one Study Instance UID - :func:`morph_files` does this.
    """
    if scrub_cfg is not None:
        Processor(scrub_cfg).process(ds)      # may raise (burned-in guard): fail closed
    else:
        _verbatim_remap(ds, uid_map if uid_map is not None else {})
    _stamp_identity(ds, ctx)
    return ds


def scrub_config(*, allow_burned_in: bool = False) -> ReceiverConfig:
    """The de-identification config used by Scrub mode: Basic Profile + structural
    sanitize + burned-in guard (fail closed unless ``allow_burned_in``)."""
    return ReceiverConfig(basic_profile=True, sanitize_enabled=True,
                          allow_burned_in=allow_burned_in)


class MorphResult:
    def __init__(self):
        self.written: list[Path] = []
        self.failed: list[str] = []
        self.study_uid: str = ""


def morph_files(files: Iterable[Path], ctx: StudyContext, out_dir: Path, *,
                verbatim: bool = False, allow_burned_in: bool = False,
                progress: Progress = _noop) -> MorphResult:
    """Morph every object of a downloaded study into ``out_dir``.

    All objects share one resolved Study Instance UID (the order's, or a freshly
    generated one if the order carried none). UID remapping is consistent across
    the study. Each object is written through ``save_dicom`` (preamble zeroed).
    A per-object failure (e.g. the burned-in guard rejecting a study with no
    blanking configured in Scrub mode) is recorded, not fatal to the batch.
    """
    files = [Path(f) for f in files]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    study_uid = ctx.study_uid or generate_uid()
    ctx = replace(ctx, study_uid=study_uid)
    scrub_cfg = None if verbatim else scrub_config(allow_burned_in=allow_burned_in)
    uid_map: dict = {}

    result = MorphResult()
    result.study_uid = study_uid
    for i, f in enumerate(files, 1):
        try:
            ds = dcmread(str(f), force=True)
            morph_dataset(ds, ctx, scrub_cfg=scrub_cfg, uid_map=uid_map)
            dest = out_dir / f.name
            save_dicom(ds, dest, enforce_file_format=True)
            result.written.append(dest)
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            result.failed.append(f"{f.name}: {str(exc).strip()[:160]}")
        progress(f"Morphed {i}/{len(files)} "
                 f"({len(result.written)} ok, {len(result.failed)} failed)")
    return result
