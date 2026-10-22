"""HL7 MLLP sender + message helpers (synthetic data only)."""

from __future__ import annotations

import socket
import threading

from dcmtoolkit.hl7 import mllp
from dcmtoolkit.hl7 import message as m

# Synthetic ORM message - NOT real patient/site data.
ORM = ("MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20260101120000|1|ORM^O01|"
       "MSGID1|T|2.5\r"
       "PID|1||TESTID^^^^MR||DOE^TEST||20000101|M\r"
       "ORC|NW|ORD1\r"
       "OBR|1|ORD1|ORD1|PROC^Example Procedure\r")

ADT = ("MSH|^~\\&|A|B|C|D|20260101120000|2|ADT^A01|MSGID2|T|2.5\r"
       "PID|1||TESTID^^^^MR||DOE^TEST\r")


def _mllp_echo_server(ack_code="AA"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def run():
        conn, _ = srv.accept()
        buf = b""
        while b"\x1c" not in buf:
            c = conn.recv(4096)
            if not c:
                break
            buf += c
        received["raw"] = buf
        ack = (f"MSH|^~\\&|RECVAPP|RECVFAC|SENDAPP|SENDFAC|20260101120001|9|"
               f"ACK|A1|T|2.5\rMSA|{ack_code}|MSGID1|ok\r")
        conn.sendall(b"\x0b" + ack.encode() + b"\x1c\x0d")
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return port, received


def test_message_helpers():
    assert m.message_type(ORM) == "ORM^O01"
    assert m.type_family(ORM) == "ORM"
    assert m.control_id(ORM) == "MSGID1"
    assert m.missing_required(ORM) == []
    # ORU requires OBX; ORM message lacks it when treated as ORU - but family is
    # taken from MSH-9, so build a broken ORU to check detection:
    broken = ORM.replace("ORM^O01", "ORU^R01")
    assert "OBX" in m.missing_required(broken)


def test_update_msh_rewrites_timestamp_and_control():
    out = m.with_updated_msh(ORM, timestamp=True, control=True)
    assert m.control_id(out) != "MSGID1"
    assert m.message_type(out) == "ORM^O01"      # type preserved
    assert m.type_family(out) == "ORM"


def test_mllp_send_accept():
    port, rec = _mllp_echo_server("AA")
    res = mllp.send_message("127.0.0.1", port, ORM, timeout=5)
    assert res.ok and res.code == "AA", (res.code, res.error)
    # The server received an MLLP-framed message.
    assert rec["raw"].startswith(b"\x0b") and b"\x1c" in rec["raw"]


def test_mllp_send_reject():
    port, _ = _mllp_echo_server("AR")
    res = mllp.send_message("127.0.0.1", port, ADT, timeout=5)
    assert not res.ok and res.code == "AR"


def test_mllp_connection_error():
    # Nothing listening on this port -> transport error, not a crash.
    res = mllp.send_message("127.0.0.1", 1, ADT, timeout=2)
    assert not res.ok and res.error
