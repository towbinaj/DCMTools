"""HL7 Minimal Lower Layer Protocol (MLLP) sender.

Frame = <SB> message <EB><CR>, where SB=0x0B, EB=0x1C, CR=0x0D. Send over TCP,
then read the ACK frame and parse MSA-1 (AA = accept, AE = error, AR = reject).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from . import message as _msg

SB = b"\x0b"
EB = b"\x1c"
CR = b"\x0d"


@dataclass
class MllpResult:
    ok: bool = False
    code: str = ""        # AA | AE | AR | "" (no/[]invalid ACK)
    text: str = ""        # MSA-3 text message, if any
    raw_ack: str = ""
    error: str = ""       # transport-level error, if the send itself failed


def _wrap(message: str) -> bytes:
    data = _msg.normalize(message)
    return SB + data.encode("utf-8", "replace") + EB + CR


def _unwrap(raw: bytes) -> str:
    body = raw
    if SB in body:
        body = body.split(SB, 1)[1]
    if EB in body:
        body = body.split(EB, 1)[0]
    return body.decode("utf-8", "replace")


def parse_ack(ack_text: str) -> tuple[str, str]:
    """(code, text) from the MSA segment of an ACK, or ('', '')."""
    sep = _msg.field_sep(ack_text)
    for seg in _msg.split_segments(ack_text):
        if seg.startswith("MSA"):
            parts = seg.split(sep)
            code = parts[1].strip() if len(parts) > 1 else ""
            text = parts[3].strip() if len(parts) > 3 else ""
            return code.upper(), text
    return "", ""


def send_message(host: str, port: int, message: str, *, timeout: float = 10.0,
                 read_ack: bool = True) -> MllpResult:
    """Send one HL7 message over MLLP and return the ACK result."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(_wrap(message))
            if not read_ack:
                return MllpResult(ok=True, code="", text="(ack not read)")
            buf = b""
            while EB + CR not in buf and EB not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError as exc:
        return MllpResult(ok=False, error=str(exc))
    if not buf:
        return MllpResult(ok=False, error="No ACK received (empty response).")
    ack = _unwrap(buf)
    code, text = parse_ack(ack)
    return MllpResult(ok=(code == "AA"), code=code, text=text, raw_ack=ack)
