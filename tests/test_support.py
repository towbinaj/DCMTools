"""Bug-report support: PHI scrubbing, address resolution, report bundle."""

from __future__ import annotations

import zipfile

from dcmtoolkit import support, config


def test_scrub_removes_names_and_long_numbers():
    text = ("Sending DOE^JANE study, AccessionNumber ACC1234567, "
            "MRN 987654321 to PACS")
    out = support.scrub(text)
    assert "DOE^JANE" not in out and "[NAME]" in out
    assert "987654321" not in out and "[NUM]" in out
    # short numbers / ordinary words survive
    assert support.scrub("3 of 12 files") == "3 of 12 files"


def test_resolve_bug_email_priority(monkeypatch):
    s = config.Settings(bug_report_email="settings@example.org")
    assert support.resolve_bug_email(s) == "settings@example.org"
    s2 = config.Settings(bug_report_email="")
    monkeypatch.setenv("DCMTOOLKIT_BUG_EMAIL", "env@example.org")
    assert support.resolve_bug_email(s2) == "env@example.org"
    monkeypatch.delenv("DCMTOOLKIT_BUG_EMAIL", raising=False)
    assert support.resolve_bug_email(s2) == support._BUILD_STAMPED_EMAIL


def test_build_report_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    s = config.Settings(my_aetitle="TESTAE")
    z = support.build_report("it broke on DOE^JANE",
                             "1. open 2. crash", include_log=True, settings=s)
    assert z.exists() and z.suffix == ".zip"
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "bug_report.txt" in names
        body = zf.read("bug_report.txt").decode("utf-8")
    # The environment section carries version + AE for triage.
    assert "TESTAE" in body
    assert "DICOM Toolkit" in body


def test_mailto_url_prefills_subject():
    url = support.mailto_url("me@example.org", support.default_subject(), "hi")
    assert url.startswith("mailto:me@example.org?")
    assert "DICOM%20Toolkit%20bug%20report" in url
