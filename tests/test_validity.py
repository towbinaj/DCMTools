"""Per-tag validity status shared by Tag Lister / Tag Compare."""

from __future__ import annotations

from dcmtoolkit.tools import validity as v
from dcmtoolkit.tools import compare
from conftest import make_ct


def test_status_buckets():
    assert v.status("PN", "DOE^JANE") == "valid"
    assert v.status("AS", "BADAGE") == "error"           # bad VR value
    assert v.status("SQ", "<seq>") == "na"
    assert v.status("PN", "X", "TagUnexpected") == "warn"
    assert v.status("PN", "", "TagEmpty") == "error"


def test_worst():
    assert v.worst("valid", "error") == "error"
    assert v.worst("valid", "warn") == "warn"
    assert v.worst("na", "valid") == "valid"
    assert v.worst("na", "na") == "na"


def test_compare_rows_carry_validity():
    a = make_ct(); b = make_ct()
    b.PatientAge = "ALSOBAD"        # error on the B side
    rows = {r.keyword: r for r in compare.diff_datasets(a, b) if r.keyword}
    assert rows["PatientName"].validity == "valid"
    assert rows["PatientAge"].validity == "error"
