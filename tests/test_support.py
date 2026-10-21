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


def test_scrub_removes_site_infrastructure():
    # IP, AE@host:port node, and user-home path components must be redacted.
    # Uses RFC 5737 documentation addresses + a fake AE, never real values.
    assert "192.0.2.27" not in support.scrub("conn 192.0.2.27 ok")
    node = support.scrub("Associating with TESTPACS@192.0.2.27:104 ...")
    assert "TESTPACS" not in node and "192.0.2.27" not in node
    assert "[NODE]" in node
    win = support.scrub(r"saved to C:\Users\jsmith\Downloads\x.csv")
    assert "jsmith" not in win and "[USER]" in win
    nix = support.scrub("saved to /home/jsmith/x.csv")
    assert "jsmith" not in nix
    unc = support.scrub(r"path \\fileserver\share")
    assert "fileserver" not in unc and "[HOST]" in unc


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


def test_inline_body_keeps_description_scrubs_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    # Seed a log line containing PHI-looking content.
    logdir = tmp_path / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "dcmtoolkit.log").write_text(
        "2026-01-01 [INFO] query DOE^JANE acc 123456789\n", encoding="utf-8")
    s = config.Settings(my_aetitle="AE1")
    body = support.inline_body("my own note with DOE^JANE", s, log_lines=20)
    # The user's own words are preserved verbatim...
    assert "my own note with DOE^JANE" in body
    # ...but the auto-added log tail is scrubbed.
    assert "123456789" not in body
    assert body.count("DOE^JANE") == 1  # only the one in the description
    assert "AE1" in body


def test_mailto_url_prefills_subject():
    url = support.mailto_url("me@example.org", support.default_subject(), "hi")
    assert url.startswith("mailto:me@example.org?")
    assert "DICOM%20Toolkit%20bug%20report" in url
