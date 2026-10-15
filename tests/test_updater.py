"""Updater version comparison (pure, no network)."""

from __future__ import annotations

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
