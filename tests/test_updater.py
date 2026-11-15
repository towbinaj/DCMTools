"""Updater version comparison (pure, no network)."""

from __future__ import annotations

import pytest

from dcmtoolkit import updater


def test_is_newer():
    assert updater.is_newer("v1.1.0", "1.0.0")
    assert updater.is_newer("1.0.1", "1.0.0")
    assert updater.is_newer("v2.0", "1.9.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("v1.0.0", "1.0.1")
    assert not updater.is_newer("v0.9", "1.0.0")


def test_parse_ver_tolerant():
    assert updater._parse_ver("v1.2.3") == (1, 2, 3)
    assert updater._parse_ver("1.2.3-beta") == (1, 2, 3)
    assert updater._parse_ver("") == (0,)


def test_can_self_update_is_false_in_dev():
    # Running under the interpreter (not frozen) -> no self-update.
    assert updater.can_self_update() is False


def test_is_github_https_accepts_only_github_over_https():
    ok = [
        "https://github.com/towbinaj/DCMTools/releases/download/v1/DICOMToolkit.exe",
        "https://api.github.com/repos/towbinaj/DCMTools/releases/latest",
        "https://objects.githubusercontent.com/github-production-release/x",
    ]
    bad = [
        "http://github.com/towbinaj/DCMTools/releases/download/v1/x.exe",  # not https
        "https://evil.example.com/DICOMToolkit.exe",                       # wrong host
        "https://github.com.evil.example/x.exe",                           # look-alike
        "ftp://github.com/x.exe",
        "",
        "not a url",
    ]
    for u in ok:
        assert updater._is_github_https(u), u
    for u in bad:
        assert not updater._is_github_https(u), u


def test_download_fails_closed_without_checksum(tmp_path):
    # Assessment F02/T20: an update with no published SHA-256 is refused before
    # any network access, rather than installed unverified.
    info = updater.UpdateInfo(
        version="9.9.9", tag="v9.9.9",
        url="https://github.com/towbinaj/DCMTools/releases/download/v9/DICOMToolkit.exe",
        size=0, sha256_url=None, notes="")
    with pytest.raises(ValueError):
        updater.download(info, tmp_path / "x.exe")


def test_download_rejects_non_github_url(tmp_path):
    info = updater.UpdateInfo(
        version="9.9.9", tag="v9.9.9",
        url="http://evil.example.com/DICOMToolkit.exe",
        size=0,
        sha256_url="https://github.com/towbinaj/DCMTools/releases/download/v9/DICOMToolkit.exe.sha256",
        notes="")
    with pytest.raises(ValueError):
        updater.download(info, tmp_path / "x.exe")
