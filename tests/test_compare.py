"""DICOM tag comparison engine."""

from __future__ import annotations

from pydicom.uid import generate_uid

from dcmtoolkit.tools import compare
from conftest import make_ct, save


def test_diff_detects_same_different_and_only():
    a = make_ct(PatientName="DOE^JANE")
    b = make_ct(PatientName="DOE^JOHN")
    a.StudyDescription = "CT HEAD"
    b.StudyDescription = "CT HEAD"       # same in both
    b.BodyPartExamined = "HEAD"          # only in B
    rows = {r.keyword: r for r in compare.diff_datasets(a, b) if r.keyword}
    assert rows["PatientName"].status == "different"
    assert rows["StudyDescription"].status == "same"
    assert rows["BodyPartExamined"].status == "only_b"


def test_load_side_series_summary(tmp_path):
    series = generate_uid()
    study = generate_uid()
    for i in range(3):
        save(make_ct(StudyInstanceUID=study, SeriesInstanceUID=series,
                     InstanceNumber=i + 1), tmp_path / f"{i}.dcm")
    ds, summ = compare.load_side([tmp_path], mode="series", label="A")
    assert summ.instances == 3 and summ.series == 1 and summ.studies == 1
    assert ds is not None


def test_enumerate_groups_series_in_one_folder(tmp_path):
    # Two series mixed in ONE folder -> two selectable groups.
    study = generate_uid()
    s1, s2 = generate_uid(), generate_uid()

    def ct(series, num, name):
        ds = make_ct(StudyInstanceUID=study, SeriesInstanceUID=series)
        ds.SeriesNumber = num
        save(ds, tmp_path / name)

    ct(s1, 1, "s1_0.dcm")
    ct(s1, 1, "s1_1.dcm")
    ct(s2, 2, "s2_0.dcm")
    groups = compare.enumerate_groups([tmp_path], "series")
    assert len(groups) == 2
    keys = {g.key for g in groups}
    assert keys == {s1, s2}
    by_key = {g.key: g for g in groups}
    assert by_key[s1].instances == 2 and by_key[s2].instances == 1
    assert any("Series 1" in g.label for g in groups)


def test_enumerate_groups_studies(tmp_path):
    st1, st2 = generate_uid(), generate_uid()
    save(make_ct(StudyInstanceUID=st1, SeriesInstanceUID=generate_uid()),
         tmp_path / "a.dcm")
    save(make_ct(StudyInstanceUID=st2, SeriesInstanceUID=generate_uid()),
         tmp_path / "b.dcm")
    groups = compare.enumerate_groups([tmp_path], "studies")
    assert {g.key for g in groups} == {st1, st2}


def test_load_side_flags_multiple_series(tmp_path):
    study = generate_uid()
    save(make_ct(StudyInstanceUID=study, SeriesInstanceUID=generate_uid()),
         tmp_path / "a.dcm")
    save(make_ct(StudyInstanceUID=study, SeriesInstanceUID=generate_uid()),
         tmp_path / "b.dcm")
    _, summ = compare.load_side([tmp_path], mode="series", label="A")
    assert summ.series == 2 and "representative" in summ.note
