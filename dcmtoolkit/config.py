"""Persistent app settings and the destinations registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import paths
from .model import Node


# ---------------------------------------------------------------------------
# Path portability: store file/folder paths that live inside DICOMToolkit-data/
# as relative, so a config file handed to a teammate "just works" when their
# data folder is elsewhere; paths outside it stay absolute. Conversion happens
# only at the persistence boundary - the rest of the app always sees absolute
# paths.
# ---------------------------------------------------------------------------
_SETTINGS_PATH_FIELDS = ("scp_save_folder", "tls_ca_file", "tls_cert_file",
                         "tls_key_file")
_RECEIVER_PATH_FIELDS = ("save_folder", "tls_cert_file", "tls_key_file",
                         "tls_ca_file", "anonymize_file", "morphing_file")


def _resolve_path(value: str) -> str:
    """Config value -> absolute path (relative values resolve against data_dir)."""
    if not value:
        return value
    p = Path(value)
    return str(p if p.is_absolute() else (paths.data_dir() / p))


def _storable_path(value: str) -> str:
    """Absolute path -> the form stored on disk: relative to DICOMToolkit-data/
    when it lives inside it (portable), otherwise left absolute."""
    if not value:
        return value
    p = Path(value)
    if p.is_absolute():
        try:
            return p.relative_to(paths.data_dir()).as_posix()
        except ValueError:
            return str(p)
    return Path(value).as_posix()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    """Global app settings persisted to settings.json."""

    my_aetitle: str = "DICOMTOOLKIT"
    appearance: str = "system"  # system | light | dark
    ui_scale: float = 1.15  # global widget/font scale for readability
    window_geometry: str = ""  # remembered "WxH+X+Y" across sessions
    last_tool: str = ""  # remembered last-selected tool panel
    send_workers: int = 4  # parallel C-STORE associations for Send
    file_workers: int = 4  # parallel workers for local file tools
    # Store Receiver defaults
    scp_aetitle: str = "STORESCP"
    scp_port: int = 104
    scp_save_folder: str = ""
    scp_folder_format: str = "PATIENT"  # UID|FLAT|MINT|MEDIS|PATIENT|STUDY
    # Outgoing (SCU) TLS - used for destinations flagged tls=True
    tls_verify: bool = True
    tls_ca_file: str = ""
    tls_cert_file: str = ""
    tls_key_file: str = ""
    # Remembered last-used destination per tool: {tool_key: destination_name}
    last_destinations: dict = field(default_factory=dict)
    # Where "Report a Bug" sends reports (blank -> falls back to env var or the
    # build-stamped address). Stored only in the local git-ignored settings.
    bug_report_email: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)


def load_settings() -> Settings:
    p = paths.config_path()
    if p.exists():
        try:
            s = Settings.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for f in _SETTINGS_PATH_FIELDS:
                setattr(s, f, _resolve_path(getattr(s, f)))
            return s
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return Settings()


def save_settings(s: Settings) -> None:
    d = s.to_dict()
    for f in _SETTINGS_PATH_FIELDS:
        d[f] = _storable_path(d.get(f, ""))
    paths.config_path().write_text(
        json.dumps(d, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Destinations registry
# ---------------------------------------------------------------------------
def load_destinations() -> list[Node]:
    p = paths.destinations_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [Node.from_dict(d) for d in raw]


def save_destinations(nodes: list[Node]) -> None:
    paths.destinations_path().write_text(
        json.dumps([n.to_dict() for n in nodes], indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Store receiver config (shared between the GUI and the Windows service)
# ---------------------------------------------------------------------------
def receiver_config_path():
    return paths.data_dir() / "receiver.json"


def load_receiver_config():
    from dataclasses import fields
    from .store.processing import ReceiverConfig
    p = receiver_config_path()
    if not p.exists():
        return ReceiverConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ReceiverConfig()
    known = {f.name for f in fields(ReceiverConfig)}
    cfg = ReceiverConfig(**{k: v for k, v in raw.items() if k in known})
    for f in _RECEIVER_PATH_FIELDS:
        setattr(cfg, f, _resolve_path(getattr(cfg, f)))
    return cfg


def save_receiver_config(cfg) -> None:
    from dataclasses import asdict
    d = asdict(cfg)
    for f in _RECEIVER_PATH_FIELDS:
        d[f] = _storable_path(d.get(f, ""))
    receiver_config_path().write_text(
        json.dumps(d, indent=2), encoding="utf-8")
