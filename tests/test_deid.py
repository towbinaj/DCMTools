"""De-identification: DICOM PS3.15 Basic Confidentiality Profile."""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.uid import generate_uid, RLELossless

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


def _nonzero_ct():
    """An uncompressed CT whose every pixel is 100 (so blanking is visible)."""
    ds = make_ct()
    arr = np.full((ds.Rows, ds.Columns), 100, dtype=np.uint16)
    ds.PixelData = arr.tobytes()
    return ds


def test_pixel_blanking_top_rows_uncompressed():
    cfg = ReceiverConfig(remove_image_top=50)   # blank the top half
    out = Processor(cfg).process(_nonzero_ct())
    arr = out.pixel_array
    assert (arr[:2, :] == 0).all()              # top 2 of 4 rows blanked
    assert (arr[2:, :] == 100).all()            # the rest untouched


def test_pixel_blanking_blanks_compressed_image():
    # Regression (assessment F01/T24): burned-in PHI must be blanked even when
    # the image is compressed - the blanking must not be silently skipped.
    ds = _nonzero_ct()
    ds.compress(RLELossless)
    assert ds.file_meta.TransferSyntaxUID == RLELossless

    out = Processor(ReceiverConfig(remove_image_top=50)).process(ds)

    # Decoded to uncompressed and the top rows are actually zeroed.
    assert not out.file_meta.TransferSyntaxUID.is_compressed
    arr = out.pixel_array
    assert (arr[:2, :] == 0).all()
    assert (arr[2:, :] == 100).all()


def test_pixel_blanking_fails_closed_when_pixels_unreadable():
    # If blanking is requested but the pixels can't be processed, the object is
    # refused (ValueError) rather than forwarded with burned-in PHI intact.
    ds = make_ct()
    ds.PixelData = b"\x00\x00"                   # too short for Rows*Columns
    with pytest.raises(ValueError):
        Processor(ReceiverConfig(remove_image_top=50)).process(ds)


def test_morph_from_csv_crosswalk(tmp_path):
    cw = tmp_path / "map.csv"
    cw.write_text("accession,name,id\nACC1,CASE-001,ID-001\n", encoding="utf-8")
    cfg = ReceiverConfig(
        morph_tags=True,
        morphing_file_format="00080050|00100010|00100020",
        morphing_file=str(cw), morph_has_header=True)
    ds = make_ct()
    ds.AccessionNumber = "ACC1"
    out = Processor(cfg).process(ds)
    assert str(out.PatientName) == "CASE-001"
    assert str(out.PatientID) == "ID-001"
    # a key not in the crosswalk is left unchanged
    ds2 = make_ct(PatientName="ORIG^NAME")
    ds2.AccessionNumber = "MISSING"
    assert str(Processor(cfg).process(ds2).PatientName) == "ORIG^NAME"
