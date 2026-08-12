"""Storage SCP (C-STORE receiver) with optional de-identification.

Wraps pynetdicom's non-blocking server. Each received object is run through a
:class:`~dcmtoolkit.store.processing.Processor` and written to disk using the
configured folder format. Runtime stats are exposed for the GUI/service to show.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydicom.dataset import Dataset
from pynetdicom import AE, evt, AllStoragePresentationContexts
from pynetdicom.sop_class import Verification

from ..logging_setup import get_logger
from ..store.naming import relative_path
from ..store.processing import Processor, ReceiverConfig

log = get_logger("scp")


@dataclass
class ReceiverStats:
    listening: bool = False
    started_at: float = 0.0
    port: int = 0
    aetitle: str = ""
    received: int = 0
    failed: int = 0
    last_object_time: float = 0.0
    last_summary: str = ""
    recent: deque = field(default_factory=lambda: deque(maxlen=10))


class StoreSCP:
    def __init__(self, cfg: ReceiverConfig,
                 on_update: Callable[[ReceiverStats], None] | None = None):
        self.cfg = cfg
        self.on_update = on_update
        self.stats = ReceiverStats(port=cfg.port, aetitle=cfg.aetitle)
        self._server = None
        self._processor: Processor | None = None
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        save_root = Path(self.cfg.save_folder or ".").expanduser()
        try:
            save_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot create save folder {save_root}: {exc}") from exc
        self._save_root = save_root
        self._processor = Processor(self.cfg)

        ae = AE(ae_title=self.cfg.aetitle)
        ae.add_supported_context(Verification)
        for cx in AllStoragePresentationContexts:
            ae.add_supported_context(cx.abstract_syntax)

        handlers = [
            (evt.EVT_C_STORE, self._handle_store),
            (evt.EVT_C_ECHO, self._handle_echo),
            (evt.EVT_ACCEPTED, self._handle_accepted),
        ]

        ssl_context = None
        if self.cfg.tls:
            if not (self.cfg.tls_cert_file and self.cfg.tls_key_file):
                raise RuntimeError(
                    "TLS receiver requires a certificate and key file.")
            from .tls import server_context
            ssl_context = server_context(
                self.cfg.tls_cert_file, self.cfg.tls_key_file,
                ca_file=self.cfg.tls_ca_file,
                require_client_cert=self.cfg.require_client_cert)

        self._server = ae.start_server(
            ("0.0.0.0", self.cfg.port), block=False, evt_handlers=handlers,
            ssl_context=ssl_context)
        self.stats.listening = True
        self.stats.started_at = time.time()
        log.info("Store SCP listening on port %d as %s (TLS=%s)",
                 self.cfg.port, self.cfg.aetitle, bool(ssl_context))
        self._emit()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self.stats.listening = False
        log.info("Store SCP stopped.")
        self._emit()

    # -- handlers --------------------------------------------------------
    def _handle_echo(self, event):
        return 0x0000

    def _handle_accepted(self, event):
        try:
            addr = event.assoc.requestor.address
            log.info("Association accepted from %s", addr)
        except Exception:  # noqa: BLE001
            pass

    def _handle_store(self, event):
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            if self._processor is not None:
                self._processor.process(ds)

            rel = relative_path(ds, self.cfg.folder_format)
            dest = self._save_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            ds.save_as(str(dest), enforce_file_format=True)

            with self._lock:
                self.stats.received += 1
                self.stats.last_object_time = time.time()
                summary = (f"{getattr(ds, 'PatientName', '?')} | "
                           f"{getattr(ds, 'Modality', '?')} | "
                           f"{getattr(ds, 'StudyDescription', '')}")
                self.stats.last_summary = summary
                self.stats.recent.appendleft(
                    (time.strftime("%H:%M:%S"), summary, str(rel)))
            self._emit()
            return 0x0000
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to store object: %s", exc)
            with self._lock:
                self.stats.failed += 1
            self._emit()
            return 0xA700  # Out of resources / cannot store

    def _emit(self) -> None:
        if self.on_update:
            try:
                self.on_update(self.stats)
            except Exception:  # noqa: BLE001
                pass
