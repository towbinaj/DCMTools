"""QA engine: base checks + conformance, send-ability, render."""

from __future__ import annotations

import shutil

import pytest
from pydicom.data import get_testdata_file
from pydicom.uid import generate_uid

from dcmtoolkit.tools import qa, conformance
from conftest import make_ct, save


def _cats(result):
    return {f.category for f in result.findings}


def test_detects_incongruent_and_duplicate(tmp_path):
    study = generate_uid()
    dup = generate_uid()
    save(make_ct(StudyInstanceUID=study, PatientName="A^A"), tmp_path / "1.dcm")
    save(make_ct(StudyInstanceUID=study, PatientName="B^B"), tmp_path / "2.dcm")
    save(make_ct(sop_instance=dup), tmp_path / "3.dcm")
    save(make_ct(sop_instance=dup), tmp_path / "4.dcm")
    res = qa.qa_scan(sorted(tmp_path.glob("*.dcm")), workers=2)
    cats = _cats(res)
    assert "incongruent" in cats
    assert "duplicate" in cats


def test_transfer_syntax_and_sop_class_report(tmp_path):
    # Two different transfer syntaxes -> both reported under 'transfer'.
    from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian
    a = make_ct(); a.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    save(a, tmp_path / "a.dcm")
    b = make_ct(); b.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    save(b, tmp_path / "b.dcm")
    res = qa.qa_scan(sorted(tmp_path.glob("*.dcm")), workers=1)
    ts = [f for f in res.findings
          if f.category == "transfer" and f.summary.startswith("Transfer syntax")]
    sop = [f for f in res.findings
           if f.category == "transfer" and f.summary.startswith("SOP class")]
    blob = " ".join(f.summary for f in ts)
    assert "Explicit VR Little Endian" in blob
    assert "Implicit VR Little Endian" in blob
    assert sop and "CT Image Storage" in " ".join(f.summary for f in sop)


def test_missing_required_tag(tmp_path):
    ds = make_ct()
    del ds.SeriesInstanceUID
    save(ds, tmp_path / "m.dcm")
    res = qa.qa_scan([tmp_path / "m.dcm"], workers=1)
    assert any(f.category == "missing" and "SeriesInstanceUID" in f.summary
               for f in res.findings)


def test_conformance_flags_incomplete(tmp_path):
    if not conformance.available():
        pytest.skip("bundled standard not available")
    save(make_ct(), tmp_path / "sparse.dcm")   # minimal CT, many Type-x missing
    off = qa.qa_scan([tmp_path / "sparse.dcm"], conformance=False, workers=1)
    on = qa.qa_scan([tmp_path / "sparse.dcm"], conformance=True, workers=1)
    assert "conformance" not in _cats(off)
    assert "conformance" in _cats(on)


def test_sendability_flags_private_sop(tmp_path):
    save(make_ct(), tmp_path / "ok.dcm")
    save(make_ct(SOPClassUID="1.2.840.10008.5.1.4.1.1.999.77"),
         tmp_path / "priv.dcm")
    res = qa.qa_scan(sorted(tmp_path.glob("*.dcm")), sendability=True, workers=2)
    # The 'transfer' category now also carries TS/SOP-class summaries, so match
    # the send-ability failure specifically.
    failed = [f for f in res.findings
              if f.category == "transfer" and "not send-able" in f.summary]
    assert len(failed) == 1 and "priv.dcm" in failed[0].summary


def test_render_flags_broken_pixels(tmp_path):
    good = save(make_ct(), tmp_path / "good.dcm")
    bad = make_ct()
    bad.Rows = 64
    bad.Columns = 64            # declares 64x64x16 but pixel data too short
    save(bad, tmp_path / "bad.dcm")
    res = qa.qa_scan([good, tmp_path / "bad.dcm"], render=True, workers=2)
    rend = [f for f in res.findings if f.category == "render"]
    assert len(rend) == 1 and "bad.dcm" in rend[0].summary


def test_render_decodes_compressed_samples(tmp_path):
    copied = 0
    for name in ("JPEG2000.dcm", "MR_small_jpeg_ls_lossless.dcm",
                 "SC_rgb_jpeg.dcm"):
        p = get_testdata_file(name)
        if p:
            shutil.copy(p, tmp_path / name)
            copied += 1
    if not copied:
        pytest.skip("pydicom test data unavailable")
    res = qa.qa_scan(sorted(tmp_path.glob("*.dcm")), render=True, workers=2)
    assert not [f for f in res.findings if f.category == "render"]


def test_render_image_grayscale_and_multiframe():
    from PIL import Image
    from dcmtoolkit.tools import render
    ds = make_ct()
    img = render.render_image(ds, 0)
    assert isinstance(img, Image.Image) and img.size == (4, 4)
    ds.NumberOfFrames = 3
    import numpy as np
    ds.PixelData = (np.arange(3 * 4 * 4, dtype="uint16").tobytes())
    assert render.frame_count(ds) == 3
    assert render.render_image(ds, 2).size == (4, 4)


def test_render_image_color_sample():
    import pytest, shutil
    from PIL import Image
    from pydicom import dcmread
    from pydicom.data import get_testdata_file
    from dcmtoolkit.tools import render
    p = get_testdata_file("SC_rgb_jpeg.dcm")
    if not p:
        pytest.skip("sample unavailable")
    img = render.render_image(dcmread(p), 0)
    assert isinstance(img, Image.Image) and img.mode == "RGB"
