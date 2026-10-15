"""Offline C-STORE send-ability self-test.

Round-trips objects through a throwaway local Storage SCP that mimics a capable
standard archive: it supports every standard storage SOP class and the common
transfer syntaxes, and discards whatever it receives. Sending through it - via
the same ``c_store`` path used for real sends - verifies each object can
actually be negotiated and transferred, catching transfer-syntax and SOP-class
problems that file-level (IOD) validation misses. No network, no external
archive, nothing written to disk.

Note: the built-in receiver is standards-based but still permissive; passing
means "transmittable to a standard archive", not "a specific strict PACS will
accept it" - for that, send to the real target with the Send tool.
"""

from __future__ import annotations

from pathlib import Path

import pydicom.uid as _uid
from pynetdicom import AE, evt
from pynetdicom.presentation import AllStoragePresentationContexts

from ..logging_setup import get_logger
from ..model import Node
from . import scu

log = get_logger("selftest")

# Transfer syntaxes a capable modern archive typically accepts.
_TS_NAMES = [
    "ImplicitVRLittleEndian", "ExplicitVRLittleEndian",
    "DeflatedExplicitVRLittleEndian", "ExplicitVRBigEndian",
    "JPEGBaseline8Bit", "JPEGExtended12Bit", "JPEGLosslessP14",
    "JPEGLossless", "JPEGLosslessSV1", "JPEGLSLossless", "JPEGLSNearLossless",
    "JPEG2000Lossless", "JPEG2000", "JPEG2000MCLossless", "JPEG2000MC",
    "RLELossless", "HTJ2KLossless", "HTJ2KLosslessRPCL", "HTJ2K",
]
_ARCHIVE_TS = [getattr(_uid, n) for n in _TS_NAMES if hasattr(_uid, n)]


def store_selftest(files, on_item=None, should_cancel=None, workers: int = 1,
                   progress=None) -> dict[str, tuple[bool, str]]:
    """Try to C-STORE each file to a built-in standard archive.

    Returns ``{file_path: (ok, code)}``; ``ok`` False means the object could
    not be negotiated/transferred (``code`` carries the status/reason).
    """
    files = [Path(f) for f in files]
    results: dict[str, tuple[bool, str]] = {}
    if not files:
        return results

    def handle_store(event):
        return 0x0000   # accept and discard

    scp_ae = AE(ae_title="SELFTESTSCP")
    for cx in AllStoragePresentationContexts:
        scp_ae.add_supported_context(cx.abstract_syntax, _ARCHIVE_TS)
    scp_ae.maximum_associations = max(1, min(int(workers), 4)) + 1
    server = scp_ae.start_server(
        ("127.0.0.1", 0), block=False,
        evt_handlers=[(evt.EVT_C_STORE, handle_store)])
    try:
        port = server.server_address[1]
        node = Node(name="selftest", aetitle="SELFTESTSCP", host="127.0.0.1",
                    port=port, timeout=60)

        def on_file(i, total, path, ok, code):
            results[str(Path(path))] = (ok, code)
            if on_item:
                on_item(i, total, path, ok, code)

        scu.c_store("SELFTESTSCU", node, files, on_file=on_file,
                    should_cancel=should_cancel,
                    workers=max(1, min(int(workers), 4)),
                    progress=progress or (lambda _m: None))
    finally:
        server.shutdown()
    return results
