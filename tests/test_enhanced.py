"""Enhanced -> classic conversion."""

from __future__ import annotations

from pydicom import dcmread

from dcmtoolkit.tools import enhanced
from conftest import make_enhanced_ct, save, CLASSIC_CT


def test_convert_flattens_and_drops_enhanced_specific(tmp_path):
    src = save(make_enhanced_ct(3), tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    res = enhanced.convert_enhanced([src], out, workers=2)
    assert res.converted == 1 and res.frames_written == 3
    files = sorted(out.glob("*.dcm"))
    assert len(files) == 3

    series = set()
    for i, f in enumerate(files):
        d = dcmread(f, force=True)
        assert str(d.SOPClassUID) == CLASSIC_CT          # classic SOP class
        assert "NumberOfFrames" not in d
        assert "PerFrameFunctionalGroupsSequence" not in d
        # standard fields flattened over
        assert list(d.PixelSpacing) == [0.5, 0.5]
        assert float(d.RescaleIntercept) == -1024
        assert float(d.KVP) == 120.0
        assert list(d.ImageType)[:2] == ["ORIGINAL", "PRIMARY"]
        assert len(d.PixelData) == 2 * 2 * 2             # one frame
        # enhanced-specific dropped
        assert "ExposureInmAs" not in d
        assert not any(e.tag.element >= 0x9000 for e in d)
        series.add(str(d.SeriesInstanceUID))
    assert len(series) == 1   # single new series


def test_convert_new_series_uid_differs_from_source(tmp_path):
    enh = make_enhanced_ct(1)
    orig_series = str(enh.SeriesInstanceUID)
    src = save(enh, tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    enhanced.convert_enhanced([src], out, new_series=True, workers=1)
    d = dcmread(next(out.glob("*.dcm")), force=True)
    assert str(d.SeriesInstanceUID) != orig_series


def test_non_enhanced_is_skipped(tmp_path):
    from conftest import make_ct
    src = save(make_ct(), tmp_path / "plain.dcm")
    res = enhanced.convert_enhanced([src], tmp_path / "out", workers=1)
    assert res.converted == 0 and res.skipped == 1
