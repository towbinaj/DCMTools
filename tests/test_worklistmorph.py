"""Worklist Morph engine: order mapping + scrub/verbatim morphing (synthetic)."""

from __future__ import annotations

from pydicom import dcmread
from pydicom.dataset import Dataset

from dcmtoolkit.model import StudyContext
from dcmtoolkit.tools import worklistmorph as wm
from dcmtoolkit.tools.worklistmorph import morph_dataset, morph_files, order_to_context, scrub_config
from conftest import make_ct, save


def _order():
    item = Dataset()
    item.PatientName = "TEST^ORDER"
    item.PatientID = "ORD-123"
    item.PatientBirthDate = "19800202"
    item.PatientSex = "F"
    item.StudyInstanceUID = "1.2.3.ORDER"
    item.AccessionNumber = "ACC-9"
    item.RequestedProcedureDescription = "CT CHEST"
    item.RequestingPhysician = "REQ^DOC"
    item.ReferringPhysicianName = "REF^DOC"
    sps = Dataset()
    sps.ScheduledProcedureStepStartDate = "20260101"
    item.ScheduledProcedureStepSequence = [sps]
    return item


def test_order_to_context_maps_fields():
    ctx = order_to_context(_order())
    assert ctx.patient_name == "TEST^ORDER"
    assert ctx.patient_id == "ORD-123"
    assert ctx.birth_date == "19800202"
    assert ctx.sex == "F"
    assert ctx.study_uid == "1.2.3.ORDER"
    assert ctx.accession == "ACC-9"
    assert ctx.study_date == "20260101"            # from the SPS start date
    assert ctx.study_description == "CT CHEST"
    assert ctx.requesting_physician == "REQ^DOC"


def test_verbatim_stamps_identity_and_mints_uids():
    src = make_ct(PatientName="REAL^PATIENT", StudyInstanceUID="1.2.orig")
    src.SeriesInstanceUID = "1.2.orig.series"
    src.InstitutionName = "REAL HOSPITAL"          # left intact in verbatim
    ctx = StudyContext(patient_name="X^Y", patient_id="PID", study_uid="1.2.order",
                       accession="A1", requesting_physician="RQ^MD")
    morph_dataset(src, ctx, scrub_cfg=None, uid_map={})

    assert str(src.PatientName) == "X^Y"
    assert src.PatientID == "PID"
    assert src.AccessionNumber == "A1"
    assert str(src.RequestingPhysician) == "RQ^MD"     # (0032,1032)
    assert src.StudyInstanceUID == "1.2.order"
    assert src.SeriesInstanceUID != "1.2.orig.series"  # fresh series UID
    assert "PixelData" in src
    assert str(src.InstitutionName) == "REAL HOSPITAL"  # verbatim keeps the rest
    assert "ScheduledStationAETitle" not in src         # never written


def test_scrub_strips_phi_then_stamps_order():
    src = make_ct(PatientName="REAL^PATIENT", PatientID="REALMRN")
    src.InstitutionName = "REAL HOSPITAL"
    ctx = StudyContext(patient_name="X^Y", patient_id="PID", study_uid="1.2.order")
    morph_dataset(src, ctx, scrub_cfg=scrub_config())

    assert str(src.PatientName) == "X^Y"           # order identity on top
    assert src.PatientID == "PID"
    assert src.StudyInstanceUID == "1.2.order"
    assert "InstitutionName" not in src            # real PHI scrubbed
    assert str(src.PatientIdentityRemoved) == "YES"


def test_morph_files_consistent_uids_across_study(tmp_path):
    study = "1.2.src.study"
    a = make_ct(StudyInstanceUID=study); a.SeriesInstanceUID = "1.2.s1"
    b = make_ct(StudyInstanceUID=study); b.SeriesInstanceUID = "1.2.s1"
    save(a, tmp_path / "a.dcm")
    save(b, tmp_path / "b.dcm")
    ctx = StudyContext(patient_name="X^Y", study_uid="1.2.order")

    res = morph_files([tmp_path / "a.dcm", tmp_path / "b.dcm"], ctx,
                      tmp_path / "out", verbatim=True)
    assert len(res.written) == 2 and not res.failed
    da, db = dcmread(res.written[0]), dcmread(res.written[1])
    assert da.StudyInstanceUID == db.StudyInstanceUID == "1.2.order"
    assert da.SeriesInstanceUID == db.SeriesInstanceUID    # same source series
    assert da.SeriesInstanceUID != "1.2.s1"                # but freshly minted
    assert da.SOPInstanceUID != db.SOPInstanceUID          # distinct instances
    # preamble neutralized on write (anti-polyglot), both modes
    raw = (tmp_path / "out" / "a.dcm").read_bytes()
    assert raw[:128] == b"\x00" * 128


def test_morph_files_resolves_study_uid_when_order_has_none(tmp_path):
    save(make_ct(), tmp_path / "a.dcm")
    ctx = StudyContext(patient_name="X^Y", study_uid="")   # order carried no UID
    res = morph_files([tmp_path / "a.dcm"], ctx, tmp_path / "out", verbatim=True)
    assert res.study_uid                                    # one was generated
    assert dcmread(res.written[0]).StudyInstanceUID == res.study_uid


def test_scrub_fails_closed_on_burned_in(tmp_path):
    src = make_ct(); src.BurnedInAnnotation = "YES"
    save(src, tmp_path / "burn.dcm")
    ctx = StudyContext(patient_name="X^Y", study_uid="1.2.order")

    # Scrub mode, no blanking, no override -> the burned-in guard rejects it.
    res = morph_files([tmp_path / "burn.dcm"], ctx, tmp_path / "o1", verbatim=False)
    assert len(res.written) == 0 and len(res.failed) == 1

    # ...and the override lets a known-clean one through.
    res2 = morph_files([tmp_path / "burn.dcm"], ctx, tmp_path / "o2",
                       verbatim=False, allow_burned_in=True)
    assert len(res2.written) == 1
