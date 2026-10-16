"""De-identification: DICOM PS3.15 Basic Confidentiality Profile."""

from __future__ import annotations

from pydicom.uid import generate_uid

from dcmtoolkit.store.processing import ReceiverConfig, Processor
from conftest import make_ct


def _phi_ct(study):
    ds = make_ct(StudyInstanceUID=study, PatientName="DOE^JANE",
                 PatientBirthDate="19800101")
    ds.InstitutionName = "ACME HOSPITAL"          # (0008,0080) X -> removed
    ds.ReferringPhysicianName = "SMITH^BOB"       # (0008,0090) Z -> emptied
    ds.PatientAddress = "1 Main St"               # (0010,1040) X -> removed
    ds.AccessionNumber = "ACC12345"               # (0008,0050) Z -> emptied
    ds.PatientID = "MRN999"                       # (0010,0020) Z -> emptied
    ds.StudyDate = "20200101"
    ds.add_new(0x00090010, "LO", "PRIVATE CREATOR")   # private
    ds.add_new(0x00091001, "LO", "secret")            # private
    return ds


def test_basic_profile_removes_phi_and_private():
    cfg = ReceiverConfig(basic_profile=True)
    proc = Processor(cfg)
    ds = proc.process(_phi_ct(generate_uid()))
    # Z-action identity attributes emptied; X-action removed
    assert str(ds.PatientName) == ""
    assert str(ds.AccessionNumber) == ""
    assert str(ds.PatientID) == ""
    assert "InstitutionName" not in ds
    assert "PatientAddress" not in ds
    # private tags gone
    assert 0x00090010 not in ds and 0x00091001 not in ds
    # de-identification markers set
    assert str(ds.PatientIdentityRemoved) == "YES"
    assert ds.DeidentificationMethodCodeSequence[0].CodeValue == "113100"


def test_basic_profile_uids_consistent_across_study():
    study = generate_uid()
    proc = Processor(ReceiverConfig(basic_profile=True))
    a = proc.process(_phi_ct(study))
    b = proc.process(_phi_ct(study))
    # UIDs changed from the original...
    assert str(a.StudyInstanceUID) != study
    # ...but identical across the two instances of the same study
    assert str(a.StudyInstanceUID) == str(b.StudyInstanceUID)
    # file_meta kept in sync with the new SOP Instance UID
    assert str(a.file_meta.MediaStorageSOPInstanceUID) == str(a.SOPInstanceUID)


def test_retain_dates_keeps_study_date_but_not_birthdate():
    proc = Processor(ReceiverConfig(basic_profile=True, retain_dates=True))
    ds = proc.process(_phi_ct(generate_uid()))
    assert str(ds.StudyDate) == "20200101"            # retained
    # birth date is de-identified (replaced with a dummy), not the real value
    assert str(getattr(ds, "PatientBirthDate", "")) != "19800101"
