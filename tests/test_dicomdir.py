"""DICOMDIR media file-set builder (dcmtoolkit.tools.dicomdir).

All synthetic - no patient data, no network.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.fileset import FileSet
from pydicom.uid import ExplicitVRLittleEndian, RLELossless, generate_uid
from pydicom.encaps import encapsulate

from dcmtoolkit.tools import dicomdir

from conftest import make_ct, save


def _pacs_tree(root: Path) -> list[Path]:
    """1 patient, 1 study, 2 series (2 images each) in nested subfolders,
    with the type-2 record elements a PACS export often omits stripped out."""
    study = generate_uid()
    paths = []
    for si, ser in enumerate([generate_uid(), generate_uid()], 1):
        for i in range(2):
            ds = make_ct(StudyInstanceUID=study, SeriesInstanceUID=ser,
                         PatientID="MRN1", PatientName="DOE^A")
            for kw in ("StudyTime", "StudyID", "SeriesNumber", "InstanceNumber"):
                if kw in ds:
                    del ds[kw]
            d = root / f"series{si}" / "sub"
            d.mkdir(parents=True, exist_ok=True)
            paths.append(Path(save(ds, d / f"img{i}.dcm")))
    return paths


def test_make_dicomdir_from_folder_groups_correctly(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _pacs_tree(src)
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out)

    assert res.added == 4
    assert res.skipped == 0
    assert res.patients == 1
    assert res.studies == 1
    assert res.series == 2
    assert res.dicomdir == out / "DICOMDIR"
    assert res.dicomdir.exists()

    # Reads back as a valid file-set with every instance indexed.
    fs = FileSet(pydicom.dcmread(str(res.dicomdir)))
    assert len(list(fs)) == 4


def test_output_paths_are_conformant_and_source_untouched(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    sources = _pacs_tree(src)
    before = {p: p.read_bytes() for p in sources}
    out = tmp_path / "out"

    dicomdir.make_dicomdir([src], out)

    # Short, uppercase, <=8-char path components (DICOM media file ID rules).
    import re
    for p in out.rglob("*"):
        if p.is_file() and p.name != "DICOMDIR":
            component = p.relative_to(out).parts[0]
            assert re.fullmatch(r"[A-Z0-9_]{1,8}", component), p

    # Sources are copied, never modified or moved.
    for p, data in before.items():
        assert p.exists()
        assert p.read_bytes() == data


def test_missing_scalar_record_elements_are_backfilled(tmp_path):
    # An instance missing StudyTime/StudyID/SeriesNumber/InstanceNumber must
    # still be indexed (via the scalar back-fill), not skipped.
    src = tmp_path / "src"
    src.mkdir()
    _pacs_tree(src)
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out)
    assert res.added == 4 and res.skipped == 0


def _basic_sr(study=None, series=None) -> Dataset:
    """A Basic Text SR with none of the SR-specific record fields - the default
    pydicom record builder can't handle it, forcing the add_custom fallback."""
    ds = Dataset()
    ds.PatientID = "MRN1"
    ds.PatientName = "DOE^A"
    ds.StudyInstanceUID = study or generate_uid()
    ds.SeriesInstanceUID = series or generate_uid()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.88.11"  # Basic Text SR
    ds.SOPInstanceUID = generate_uid()
    ds.Modality = "SR"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    return ds


def test_exotic_sop_class_uses_custom_fallback(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    study = generate_uid()
    img = make_ct(StudyInstanceUID=study)
    save(img, src / "image.dcm")
    sr = _basic_sr(study=study)
    sr.save_as(str(src / "report.dcm"), enforce_file_format=True)
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out)

    assert res.added == 2          # both indexed
    assert res.custom == 1         # the SR needed the fallback
    assert res.skipped == 0
    fs = FileSet(pydicom.dcmread(str(res.dicomdir)))
    assert {i.SOPClassUID for i in fs} == {img.SOPClassUID, sr.SOPClassUID}


def test_compressed_transfer_syntax_passes_through(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    ds = make_ct()
    ds.PixelData = encapsulate([ds.PixelData])
    ds["PixelData"].is_undefined_length = True
    ds.file_meta.TransferSyntaxUID = RLELossless
    ds.save_as(str(src / "rle.dcm"), enforce_file_format=True)
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out)
    assert res.added == 1

    fs = FileSet(pydicom.dcmread(str(res.dicomdir)))
    inst = next(iter(fs))
    assert inst.load().file_meta.TransferSyntaxUID == RLELossless


def test_non_dicom_junk_and_existing_dicomdir_are_skipped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    save(make_ct(), src / "good.dcm")
    (src / "notes.txt").write_text("not dicom")        # wrong extension -> ignored
    (src / "junk").write_bytes(b"\x00\x01\x02\x03")     # no-ext junk -> tried, skipped
    (src / "DICOMDIR").write_bytes(b"\x00" * 10)        # pre-existing index -> never folded in
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out)
    assert res.added == 1
    assert res.skipped >= 1        # the junk file was attempted and skipped


def test_nonempty_output_dir_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    save(make_ct(), src / "a.dcm")
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("busy")

    with pytest.raises(ValueError):
        dicomdir.make_dicomdir([src], out)


def test_clean_fileset_id():
    assert dicomdir._clean_fileset_id("my toolkit!!") == "MYTOOLKIT"
    assert dicomdir._clean_fileset_id("") == "DCMTOOLKIT"
    assert dicomdir._clean_fileset_id("a" * 40) == "A" * 16


def test_unique_dir(tmp_path):
    first = dicomdir.unique_dir(tmp_path, "fileset")
    assert first == tmp_path / "fileset"
    first.mkdir()
    second = dicomdir.unique_dir(tmp_path, "fileset")
    assert second == tmp_path / "fileset_1"


def test_cancel_before_write_writes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _pacs_tree(src)
    out = tmp_path / "out"

    res = dicomdir.make_dicomdir([src], out, should_cancel=lambda: True)
    assert res.dicomdir is None
    assert not (out / "DICOMDIR").exists()
