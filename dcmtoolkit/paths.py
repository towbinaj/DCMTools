"""Filesystem locations for the app.

The app is designed to run as a portable, double-click executable. Config and
logs live in a ``DICOMToolkit-data`` folder next to the executable when that
location is writable (portable mode), otherwise they fall back to the user's
%APPDATA% (installed mode). This keeps a copied-to-a-USB-stick deployment
self-contained while still working when installed to a read-only Program Files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory containing the running program (exe dir, or project root)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # dcmtoolkit/paths.py -> project root is two levels up
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory holding bundled read-only resources (icons, defaults).

    Under PyInstaller onefile this is the temp extraction dir (``sys._MEIPASS``).
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Directory for user config, destinations, and logs.

    Prefers a portable folder next to the executable; falls back to %APPDATA%.
    Can be overridden with the DCMTOOLKIT_DATA environment variable.
    """
    override = os.environ.get("DCMTOOLKIT_DATA")
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d

    portable = app_dir() / "DICOMToolkit-data"
    if _writable(portable):
        return portable

    base = os.environ.get("APPDATA") or str(Path.home())
    fallback = Path(base) / "DICOMToolkit"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def config_path() -> Path:
    return data_dir() / "settings.json"


def destinations_path() -> Path:
    return data_dir() / "destinations.json"


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
