"""File engines: dicom discovery, zip expansion, modify, split."""

from __future__ import annotations

import io
import zipfile

from pydicom import dcmread

from dcmtoolkit.tools import fileops
from conftest import make_ct, make_enhanced_ct, save


def _dcm_bytes(**over) -> bytes:
    buf = io.BytesIO()
    make_ct(**over).save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def test_find_dicom_files_recurses(tmp_path):
    (tmp_path / "a").mkdir()
    save(make_ct(), tmp_path / "a" / "1.dcm")
    save(make_ct(), tmp_path / "2.dcm")
    (tmp_path / "note.txt").write_text("junk")
    found = fileops.find_dicom_files(tmp_path)
    assert len(found) == 2


def test_expand_sources_extracts_zip(tmp_path):
    z = tmp_path / "study.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("s/img1.dcm", _dcm_bytes())
        zf.writestr("s/IM2", _dcm_bytes())        # no extension, DICM magic
        zf.writestr("readme.txt", b"not dicom")   # junk, skipped
    out = fileops.expand_sources([z])
    assert len(out) == 2
    assert all(fileops._is_dicom_file(p) for p in out)


def test_expand_sources_nested_zip_and_folder(tmp_path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.dcm", _dcm_bytes())
    folder = tmp_path / "drop"
    folder.mkdir()
    save(make_ct(), folder / "loose.dcm")
    with zipfile.ZipFile(folder / "outer.zip", "w") as zf:
        zf.writestr("a.dcm", _dcm_bytes())
        zf.writestr("nested.zip", inner.read_bytes())
    out = fileops.expand_sources([folder])
    # loose + a.dcm + nested deep.dcm
    assert len(out) == 3


def test_modify_set_and_remove(tmp_path):
    p = save(make_ct(PatientID="OLD"), tmp_path / "x.dcm")
    ops = [fileops.ModifyOp(tag="00100020", action="set", value="NEW"),
           fileops.ModifyOp(tag="00080050", action="remove")]  # AccessionNumber
    res = fileops.modify_files([p], ops, in_place=True)
    assert res.changed == 1 and res.failed == 0
    ds = dcmread(p, force=True)
    assert str(ds.PatientID) == "NEW"
    assert "AccessionNumber" not in ds


def test_split_multiframe(tmp_path):
    mf = make_ct()
    mf.NumberOfFrames = 3
    mf.PixelData = bytes(3 * 4 * 4 * 2)
    src = save(mf, tmp_path / "mf.dcm")
    out = tmp_path / "split"
    res = fileops.split_multiframe([src], out)
    assert res.frames_written == 3
    frames = sorted(out.glob("*.dcm"))
    assert len(frames) == 3
    for f in frames:
        assert int(getattr(dcmread(f, force=True), "NumberOfFrames", 1)) == 1
