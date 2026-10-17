"""Anonymization profiles: persistence + custom-rule engine integration."""

from __future__ import annotations

from pydicom.uid import generate_uid

from dcmtoolkit import anon_profiles as ap
from dcmtoolkit.store.processing import ReceiverConfig, Processor
from conftest import make_ct


def test_profiles_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    profs = [
        ap.AnonProfile("P1", basic_profile=True, retain_dates=True,
                       rules=[ap.AnonRule("00100020", "KEEP"),
                              ap.AnonRule("00081030", "REPLACE", "X")]),
    ]
    ap.save_profiles(profs)
    loaded = ap.load_profiles()
    assert [p.name for p in loaded] == ["P1"]
    p = loaded[0]
    assert p.retain_dates is True
    assert p.custom_rules() == [("00100020", "KEEP", ""),
                                ("00081030", "REPLACE", "X")]


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    names = [p.name for p in ap.load_profiles()]
    assert any("Basic" in n for n in names)


def test_keyword_lookup():
    assert ap.keyword_for("00100010") == "PatientName"
    assert ap.keyword_for("00100020") == "PatientID"
    assert ap.keyword_for("notahex") == ""


def test_profile_rules_drive_engine():
    prof = ap.AnonProfile("x", basic_profile=True, rules=[
        ap.AnonRule("00100020", "KEEP"),
        ap.AnonRule("00081030", "REPLACE", "ANON STUDY"),
        ap.AnonRule("00080050", "REMOVE"),
    ])
    ds = make_ct(PatientName="DOE^JANE", PatientID="MRN1")
    ds.AccessionNumber = "ACC9"
    ds.StudyDescription = "SECRET"
    cfg = ReceiverConfig(basic_profile=True, custom_rules=prof.custom_rules())
    out = Processor(cfg).process(ds)
    assert str(out.PatientName) == ""          # basic profile emptied it
    assert str(out.PatientID) == "MRN1"        # KEEP exempted it
    assert str(out.StudyDescription) == "ANON STUDY"   # REPLACE
    assert "AccessionNumber" not in out        # REMOVE
