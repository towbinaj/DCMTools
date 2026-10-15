"""Enhanced -> classic conversion (incl. stack splitting)."""

from __future__ import annotations

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from dcmtoolkit.tools import enhanced
from conftest import make_enhanced_ct, make_ct, save, CLASSIC_CT, ENH_CT


def _item(**kw):
    d = Dataset()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_convert_flattens_and_drops_enhanced_specific(tmp_path):
    src = save(make_enhanced_ct(3), tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    res = enhanced.convert_enhanced([src], out, workers=2)
    assert res.converted == 1 and res.frames_written == 3
    files = sorted(out.rglob("*.dcm"))           # output nested under out/enh/
    assert len(files) == 3

    series = set()
    for f in files:
        d = dcmread(f, force=True)
        assert str(d.SOPClassUID) == CLASSIC_CT
        assert "NumberOfFrames" not in d
        assert "PerFrameFunctionalGroupsSequence" not in d
        assert list(d.PixelSpacing) == [0.5, 0.5]
        assert float(d.RescaleIntercept) == -1024
        assert float(d.KVP) == 120.0
        assert list(d.ImageType)[:2] == ["ORIGINAL", "PRIMARY"]
        assert len(d.PixelData) == 2 * 2 * 2
        assert "ExposureInmAs" not in d
        assert not any(e.tag.element >= 0x9000 for e in d)
        series.add(str(d.SeriesInstanceUID))
    assert len(series) == 1                       # one series (single stack)


def test_convert_new_series_uid_differs_from_source(tmp_path):
    enh = make_enhanced_ct(1)
    orig = str(enh.SeriesInstanceUID)
    src = save(enh, tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    enhanced.convert_enhanced([src], out, new_series=True, workers=1)
    d = dcmread(next(out.rglob("*.dcm")), force=True)
    assert str(d.SeriesInstanceUID) != orig


def test_non_enhanced_is_skipped(tmp_path):
    src = save(make_ct(), tmp_path / "plain.dcm")
    res = enhanced.convert_enhanced([src], tmp_path / "out", workers=1)
    assert res.converted == 0 and res.skipped == 1


def _two_stack_enhanced():
    """Enhanced CT whose 4 frames belong to 2 stacks (StackID 1 and 2)."""
    ds = make_enhanced_ct(1)
    ds.NumberOfFrames = 4
    ds.PixelData = bytes(4 * 2 * 2 * 2)
    ds.SeriesNumber = 3
    pf = []
    for stack in ("1", "2"):
        for pos in (1, 2):
            pf.append(_item(
                PlanePositionSequence=[_item(ImagePositionPatient=[0, 0, pos])],
                FrameContentSequence=[_item(StackID=stack,
                                            InStackPositionNumber=pos)]))
    ds.PerFrameFunctionalGroupsSequence = pf
    return ds


def test_split_stacks_makes_two_series(tmp_path):
    src = save(_two_stack_enhanced(), tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    res = enhanced.convert_enhanced([src], out, split_stacks=True, workers=1)
    assert res.frames_written == 4

    # two series subfolders, two files each
    subdirs = sorted(p for p in (out / "enh").iterdir() if p.is_dir())
    assert len(subdirs) == 2
    series_uids, series_nums = set(), set()
    for d in subdirs:
        files = sorted(d.glob("*.dcm"))
        assert len(files) == 2
        inst = []
        for f in files:
            ds = dcmread(f, force=True)
            series_uids.add(str(ds.SeriesInstanceUID))
            series_nums.add(int(ds.SeriesNumber))
            inst.append(int(ds.InstanceNumber))
        assert sorted(inst) == [1, 2]            # clean 1..N per series
    assert len(series_uids) == 2                 # distinct series UIDs
    assert len(series_nums) == 2                 # distinct series numbers


def test_no_split_keeps_single_series(tmp_path):
    src = save(_two_stack_enhanced(), tmp_path / "enh.dcm")
    out = tmp_path / "classic"
    enhanced.convert_enhanced([src], out, split_stacks=False, workers=1)
    uids = {str(dcmread(f, force=True).SeriesInstanceUID)
            for f in out.rglob("*.dcm")}
    assert len(uids) == 1                         # all 4 frames in one series
