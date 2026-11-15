"""Config persistence: receiver allowlist + team-shareable relative paths.

All synthetic - no patient data, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from dcmtoolkit import config
from dcmtoolkit.store.processing import ReceiverConfig


def test_receiver_allowlist_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    config.save_receiver_config(
        ReceiverConfig(aetitle="A", allowed_calling_aes=["AE1", "AE2"]))
    raw = json.loads(config.receiver_config_path().read_text(encoding="utf-8"))
    assert raw["allowed_calling_aes"] == ["AE1", "AE2"]
    assert config.load_receiver_config().allowed_calling_aes == ["AE1", "AE2"]


def test_paths_inside_data_dir_are_stored_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    data = config.paths.data_dir()
    cert = data / "certs" / "server.pem"
    cfg = ReceiverConfig(tls_cert_file=str(cert),
                         save_folder=str(data / "incoming"))
    config.save_receiver_config(cfg)

    raw = json.loads(config.receiver_config_path().read_text(encoding="utf-8"))
    # Stored relative so a copied config works on a teammate's machine...
    assert raw["tls_cert_file"] == "certs/server.pem"
    assert raw["save_folder"] == "incoming"
    # ...and resolved back to absolute in memory.
    back = config.load_receiver_config()
    assert Path(back.tls_cert_file) == cert
    assert Path(back.save_folder) == data / "incoming"


def test_paths_outside_data_dir_stay_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path / "data"))
    outside = tmp_path / "elsewhere" / "ca.pem"
    config.save_receiver_config(ReceiverConfig(tls_ca_file=str(outside)))
    raw = json.loads(config.receiver_config_path().read_text(encoding="utf-8"))
    assert raw["tls_ca_file"] == str(Path(outside))          # unchanged, absolute


def test_settings_paths_round_trip_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tmp_path))
    data = config.paths.data_dir()
    s = config.Settings(scp_save_folder=str(data / "incoming"),
                        tls_ca_file=str(data / "ca" / "root.pem"))
    config.save_settings(s)
    raw = json.loads(config.paths.config_path().read_text(encoding="utf-8"))
    assert raw["scp_save_folder"] == "incoming"
    assert raw["tls_ca_file"] == "ca/root.pem"
    back = config.load_settings()
    assert Path(back.scp_save_folder) == data / "incoming"
