"""Core data models shared across the toolkit."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, asdict, field


AE_TITLE_MAX = 16


@dataclass
class Node:
    """A remote DICOM peer (PACS, VNA, workstation, etc.).

    ``name`` is the friendly label shown in dropdowns; the DICOM identity is the
    (aetitle, host, port) triple.
    """

    name: str
    aetitle: str
    host: str
    port: int
    note: str = ""
    timeout: int = 30
    tls: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=str(d.get("name", "")).strip(),
            aetitle=str(d.get("aetitle", "")).strip(),
            host=str(d.get("host", "")).strip(),
            port=int(d.get("port", 104) or 104),
            note=str(d.get("note", "")).strip(),
            timeout=int(d.get("timeout", 30) or 30),
            tls=bool(d.get("tls", False)),
        )

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means valid."""
        problems: list[str] = []
        if not self.name:
            problems.append("Name is empty.")
        if not self.aetitle:
            problems.append("AE Title is empty.")
        elif len(self.aetitle) > AE_TITLE_MAX:
            problems.append(
                f"AE Title '{self.aetitle}' exceeds {AE_TITLE_MAX} characters."
            )
        if not self.host:
            problems.append("Host is empty.")
        else:
            try:
                ipaddress.ip_address(self.host)
            except ValueError:
                # Not an IP literal - allow hostnames but flag obvious typos
                if ".." in self.host or self.host.endswith("."):
                    problems.append(f"Host '{self.host}' looks malformed.")
        if not (1 <= int(self.port) <= 65535):
            problems.append(f"Port {self.port} is out of range (1-65535).")
        return problems

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.aetitle}@{self.host}:{self.port})"
