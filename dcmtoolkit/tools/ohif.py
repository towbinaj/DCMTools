"""Open local DICOM files in the bundled OHIF viewer - fully offline.

OHIF is a static web app, so no PACS / DICOMweb backend is needed: a tiny HTTP
server bound to 127.0.0.1 serves the OHIF build plus the selected files, and a
``dicomjson`` manifest (study -> series -> instance metadata + file URL, built
here from the DICOM headers) tells OHIF what to load. Tk can't host a modern
web app, so the viewer opens in a chrome-less Edge/Chrome ``--app`` window
(falling back to the default browser).

Only files explicitly registered in a manifest are reachable, each behind an
unguessable token - the server never maps URLs onto arbitrary disk paths.
"""

from __future__ import annotations

import gzip
import io
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlsplit

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.valuerep import PersonName

from .. import paths
from ..logging_setup import get_logger

_log = get_logger("ohif")

#: Folder (under the resource dir) holding the OHIF production build.
OHIF_SUBDIR = Path("assets") / "ohif-viewer"

# Binary/bulk VRs are never needed for display metadata (the pixels come from
# the file itself) and would bloat the manifest - skip them.
_BULK_VRS = {"OB", "OD", "OF", "OL", "OV", "OW", "UN"}
_INT_VRS = {"IS", "SL", "SS", "SV", "UL", "US", "UV", "AT"}
_FLOAT_VRS = {"DS", "FL", "FD"}

# Our own app-config.js: only the dicomjson data source, no study list (it
# would be empty), nothing pointing at a remote server.
_APP_CONFIG = """/** DICOM Toolkit: offline OHIF config (generated). */
window.config = {
  name: 'dcmtoolkit',
  routerBasename: null,
  extensions: [],
  modes: [],
  customizationService: {},
  showStudyList: false,
  maxNumberOfWebWorkers: 3,
  // Decoded-pixel cache per viewer window (OHIF default 3 GB). Each Open
  // makes a new window, so several open at once would add up; 1 GB still
  // holds ~2000 512x512 CT slices and evicts least-recently-used beyond that.
  maxCacheSize: 1073741824,
  showWarningMessageForCrossOrigin: false,
  showCPUFallbackMessage: true,
  showLoadingIndicator: true,
  strictZSpacingForVolumeViewport: true,
  groupEnabledModesFirst: true,
  investigationalUseDialog: { option: 'never' },
  maxNumRequests: { interaction: 100, thumbnail: 5, prefetch: 25 },
  showErrorDetails: 'always',
  // OHIF calls this for every failed image load; without it OHIF's own
  // handler throws 'handler is not a function' and hides the real error.
  httpErrorHandler: error => console.warn('Image load failed:', error),
  defaultDataSourceName: 'dicomjson',
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomjson',
      sourceName: 'dicomjson',
      configuration: { friendlyName: 'DICOM Toolkit local files', name: 'json' },
    },
  ],
};
"""


def ohif_dir() -> Path:
    return paths.resource_dir() / OHIF_SUBDIR


def available() -> bool:
    """True when the OHIF build is present (bundled or fetched for dev)."""
    return (ohif_dir() / "index.html").is_file()


# -- manifest ------------------------------------------------------------

def _value(elem):
    """One DataElement -> JSON value in OHIF's 'naturalized' shape.

    Single values are scalars, multi-values lists, sequences lists of dicts,
    numbers are numbers, person names plain strings.
    """
    vr = elem.VR
    if vr == "SQ":
        return [naturalize(item) for item in elem.value]
    val = elem.value
    if val is None or val == "":
        return None
    items = list(val) if isinstance(val, (MultiValue, list, tuple)) else [val]
    out = []
    for v in items:
        if vr in _INT_VRS:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        elif vr in _FLOAT_VRS:
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
        elif isinstance(v, PersonName):
            v = str(v)
        elif isinstance(v, bytes):
            v = v.decode("latin-1", "replace")
        else:
            v = str(v)
        out.append(v)
    if not out:
        return None
    return out[0] if len(out) == 1 else out


def naturalize(ds: Dataset) -> dict:
    """Public, non-bulk attributes keyed by keyword (private tags dropped)."""
    out = {}
    for elem in ds:
        if elem.VR in _BULK_VRS or not elem.keyword or elem.tag.is_private:
            continue
        try:
            v = _value(elem)
        except Exception:  # noqa: BLE001 - one odd element must not sink the file
            continue
        if v is not None:
            out[elem.keyword] = v
    return out


_VOI_FUNCTIONS = {"LINEAR", "LINEAR_EXACT", "SIGMOID"}


def _fix_voi_function(meta: dict) -> None:
    """Send VOILUTFunction the way Cornerstone reads it, or drop it.

    The image loader takes ``voiLUTFunction[0]``, so a plain string "LINEAR"
    became "L" and every such image failed with 'Invalid VOI LUT function'.
    Always send a one-item list holding exactly LINEAR / LINEAR_EXACT /
    SIGMOID (first value, case-folded); drop anything else - missing means
    LINEAR, the DICOM default.
    """
    v = meta.get("VOILUTFunction")
    if v is None:
        return
    if isinstance(v, list):
        v = v[0] if v else ""
    v = str(v).strip().upper()
    if v in _VOI_FUNCTIONS:
        meta["VOILUTFunction"] = [v]
    else:
        del meta["VOILUTFunction"]


def _frames(ds: Dataset) -> int:
    try:
        return max(1, int(getattr(ds, "NumberOfFrames", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _instance_number(meta: dict) -> int:
    try:
        return int(meta.get("InstanceNumber"))
    except (TypeError, ValueError):
        return 10**9


@dataclass
class _Obj:
    """One DICOM object, its header already serialised (dicts are dropped
    straight away - holding 14k parsed headers as Python objects cost
    hundreds of MB)."""
    sort_key: tuple
    sop: str
    meta_json: str
    base: str
    frames: int


def _entries_json(o: _Obj):
    """Manifest entries for one object (JSON text): one per frame.

    OHIF keeps only the first entry per SOPInstanceUID as the instance and
    uses the rest just for their per-frame URL, so only the first carries the
    full header. Repeating it per frame made the manifest grow with
    frames x header size - quadratic for enhanced objects, whose per-frame
    functional groups grow with the frame count (gigabytes at a few thousand
    frames).
    """
    if o.frames == 1:
        yield f'{{"metadata":{o.meta_json},"url":{json.dumps(o.base)}}}'
        return
    stub = json.dumps({"SOPInstanceUID": o.sop})
    for fr in range(1, o.frames + 1):
        meta = o.meta_json if fr == 1 else stub
        yield f'{{"metadata":{meta},"url":{json.dumps(f"{o.base}?frame={fr}")}}}'


@dataclass
class Manifest:
    studies: list[dict]          # summaries; each series has "objects"
    files: dict[str, Path] = field(default_factory=dict)   # token -> file
    skipped: int = 0

    @property
    def study_uids(self) -> list[str]:
        return [s["StudyInstanceUID"] for s in self.studies]

    @property
    def instance_count(self) -> int:
        return len(self.files)

    def chunks(self):
        """The dicomjson manifest as a stream of JSON text pieces."""
        yield '{"studies":['
        for i, st in enumerate(self.studies):
            head = {k: v for k, v in st.items() if k != "series"}
            yield ("," if i else "") + json.dumps(head)[:-1] + ',"series":['
            for j, se in enumerate(st["series"]):
                head = {k: v for k, v in se.items() if k != "objects"}
                yield ("," if j else "") + json.dumps(head)[:-1] + ',"instances":['
                first = True
                for o in se["objects"]:
                    for e in _entries_json(o):
                        yield e if first else "," + e
                        first = False
                yield "]}"
            yield "]}"
        yield "]}"

    @property
    def data(self) -> dict:
        """Parsed manifest (for tests/inspection - prepare() streams)."""
        return json.loads("".join(self.chunks()))


def build_manifest(files: Iterable[Path], file_url) -> Manifest:
    """Read headers and build a dicomjson manifest.

    ``file_url(token)`` returns the absolute http URL the token is served at.
    Multi-frame objects get one entry per frame (``?frame=N``, 1-based), which
    is how OHIF's dicomjson source addresses individual frames; only the first
    carries the header (see ``_entries_json``).
    Non-DICOM / pixel-less files are skipped (counted in ``skipped``).
    """
    studies: dict[str, dict] = {}
    series: dict[tuple[str, str], dict] = {}
    tokens: dict[str, Path] = {}
    skipped = 0

    for f in files:
        f = Path(f)
        try:
            ds = dcmread(str(f), stop_before_pixels=True, force=True)
        except Exception:  # noqa: BLE001 - not DICOM
            skipped += 1
            continue
        st = str(getattr(ds, "StudyInstanceUID", "") or "")
        se = str(getattr(ds, "SeriesInstanceUID", "") or "")
        sop = str(getattr(ds, "SOPInstanceUID", "") or "")
        if not (st and se and sop and "Rows" in ds):
            skipped += 1    # OHIF can't place or draw it (e.g. SR, DICOMDIR)
            continue
        meta = naturalize(ds)
        _fix_voi_function(meta)
        if st not in studies:
            studies[st] = {
                "StudyInstanceUID": st,
                "StudyDate": meta.get("StudyDate", ""),
                "StudyTime": meta.get("StudyTime", ""),
                "StudyDescription": meta.get("StudyDescription", ""),
                "PatientName": meta.get("PatientName", ""),
                "PatientID": meta.get("PatientID", ""),
                "AccessionNumber": meta.get("AccessionNumber", ""),
                "PatientAge": meta.get("PatientAge", ""),
                "PatientSex": meta.get("PatientSex", ""),
                "series": [],
            }
        if (st, se) not in series:
            s = {
                "SeriesInstanceUID": se,
                "SeriesDescription": meta.get("SeriesDescription", ""),
                "SeriesNumber": meta.get("SeriesNumber", ""),
                "SeriesDate": meta.get("SeriesDate", ""),
                "SeriesTime": meta.get("SeriesTime", ""),
                "Modality": meta.get("Modality", ""),
                "objects": [],
            }
            series[(st, se)] = s
            studies[st]["series"].append(s)
        token = secrets.token_urlsafe(16)
        tokens[token] = f
        base = "dicomweb:" + file_url(token)
        series[(st, se)]["objects"].append(_Obj(
            (_instance_number(meta), sop, base), sop,
            json.dumps(meta, separators=(",", ":")), base, _frames(ds)))

    for s in series.values():
        s["objects"].sort(key=lambda o: o.sort_key)
    for st in studies.values():
        st["series"].sort(key=lambda s: (
            s["SeriesNumber"] if isinstance(s["SeriesNumber"], int) else 10**9))
        mods = sorted({s["Modality"] for s in st["series"] if s["Modality"]})
        st["Modalities"] = "\\".join(mods)
        st["NumInstances"] = len({o.sop for s in st["series"]
                                  for o in s["objects"]})
    return Manifest(list(studies.values()), tokens, skipped)


# -- local server --------------------------------------------------------

class _Handler(SimpleHTTPRequestHandler):
    server: "_ViewerHTTPServer"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(self.server_root()), **kw)

    @classmethod
    def server_root(cls) -> Path:
        return ohif_dir()

    def log_message(self, fmt, *args) -> None:   # keep stderr quiet (GUI exe)
        pass

    def end_headers(self) -> None:
        # Cross-origin isolation enables SharedArrayBuffer (faster volume
        # loading); everything is same-origin so it costs nothing.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_bytes(self, body: bytes, ctype: str, encoding: str = "") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urlsplit(self.path).path
        srv = self.server
        if path == "/app-config.js":
            return self._send_bytes(_APP_CONFIG.encode(), "text/javascript")
        if path.startswith("/dcmtk/manifest/"):
            mid = path.rsplit("/", 1)[-1].removesuffix(".json")
            body = srv.manifests.get(mid)
            if body is None:
                return self.send_error(404)
            return self._send_bytes(body, "application/json", "gzip")
        if path.startswith("/dcmtk/file/"):
            f = srv.files.get(path.rsplit("/", 1)[-1])
            if f is None:
                return self.send_error(404)
            # Stream rather than read_bytes(): objects can be gigabytes.
            try:
                fh = open(f, "rb")
            except OSError:
                return self.send_error(404)
            with fh:
                self.send_response(200)
                self.send_header("Content-Type", "application/dicom")
                self.send_header("Content-Length",
                                 str(os.fstat(fh.fileno()).st_size))
                self.end_headers()
                shutil.copyfileobj(fh, self.wfile, 1 << 20)
            return None
        # OHIF is a single-page app: client-side routes (e.g. /viewer/dicomjson)
        # have no file behind them and must get index.html.
        root = self.server_root().resolve()
        target = (root / path.lstrip("/")).resolve()
        if root not in target.parents or not target.is_file():
            self.path = "/index.html"
        return super().do_GET()


class _ViewerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.manifests: dict[str, bytes] = {}
        self.files: dict[str, Path] = {}
        self.opens: deque[tuple[str, list[str]]] = deque()  # (mid, tokens)

    def handle_error(self, request, client_address) -> None:
        # The viewer routinely cancels image downloads it no longer needs
        # (scrolled past, window closed); that is not an error. Anything else
        # goes to the app log rather than stderr, which a windowed exe lacks.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        _log.warning("OHIF viewer server error", exc_info=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


#: How many recent "Open in OHIF" requests stay servable. Older ones are
#: forgotten so the app's memory doesn't grow over a long session; a viewer
#: window that old just can't fetch images it hadn't loaded yet.
MAX_OPENS = 10

_server: _ViewerHTTPServer | None = None
_lock = threading.Lock()

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")


def server() -> _ViewerHTTPServer:
    """The shared local server, started on first use (random free port)."""
    global _server
    with _lock:
        if _server is None:
            _server = _ViewerHTTPServer()
            threading.Thread(target=_server.serve_forever, name="ohif-http",
                             daemon=True).start()
            _log.info("OHIF viewer server on %s", _server.base_url)
        return _server


def shutdown() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None


def _gzip_chunks(chunks) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        for c in chunks:
            gz.write(c.encode())
    return buf.getvalue()


def prepare(files: Iterable[Path]) -> tuple[str, Manifest]:
    """Register ``files`` with the local server; return (viewer URL, manifest)."""
    srv = server()
    m = build_manifest(files, lambda t: f"{srv.base_url}/dcmtk/file/{t}")
    if not m.studies:
        raise ValueError("No displayable DICOM images found.")
    mid = secrets.token_urlsafe(12)
    with _lock:
        srv.files.update(m.files)
        # Kept gzipped (~10x smaller; headers are repetitive): a 14k-object
        # load is tens of MB of JSON and up to MAX_OPENS of them are kept.
        srv.manifests[mid] = _gzip_chunks(m.chunks())
        srv.opens.append((mid, list(m.files)))
        while len(srv.opens) > MAX_OPENS:
            old_mid, old_tokens = srv.opens.popleft()
            srv.manifests.pop(old_mid, None)
            for t in old_tokens:
                srv.files.pop(t, None)
    manifest_url = f"{srv.base_url}/dcmtk/manifest/{mid}.json"
    url = (f"{srv.base_url}/viewer/dicomjson?url={quote(manifest_url, safe='')}"
           f"&StudyInstanceUIDs={quote(','.join(m.study_uids), safe='')}")
    return url, m


# -- window --------------------------------------------------------------

def _app_browser() -> str | None:
    """Path to Edge/Chrome, which can open a chrome-less ``--app`` window."""
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser",
                 "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def open_window(url: str) -> None:
    """Open ``url`` in an app-style window, else the default browser."""
    exe = _app_browser()
    if exe:
        # A dedicated profile makes --app open a fresh window even when the
        # browser is already running, and keeps viewer state out of the
        # user's normal browsing profile.
        profile = paths.data_dir() / "viewer-profile"
        try:
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                              "--no-first-run", "--no-default-browser-check"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError as exc:
            _log.warning("Could not start %s (%s); using default browser",
                         exe, exc)
    webbrowser.open(url)
