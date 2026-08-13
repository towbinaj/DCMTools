"""Persistent app settings and the destinations registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field

from . import paths
from .model import Node


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
            return Settings.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return Settings()


def save_settings(s: Settings) -> None:
    paths.config_path().write_text(
        json.dumps(s.to_dict(), indent=2), encoding="utf-8"
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
    return ReceiverConfig(**{k: v for k, v in raw.items() if k in known})


def save_receiver_config(cfg) -> None:
    from dataclasses import asdict
    receiver_config_path().write_text(
        json.dumps(asdict(cfg), indent=2), encoding="utf-8")
