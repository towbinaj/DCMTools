"""Bug-report support: diagnostics bundling, PHI scrubbing, and mailto.

The maintainer's address is deliberately NOT hard-coded in the (public) source.
It is resolved at runtime from, in order:

1. the user's local, git-ignored settings (``bug_report_email``);
2. the ``DCMTOOLKIT_BUG_EMAIL`` environment variable;
3. ``_BUILD_STAMPED_EMAIL`` - left blank here, optionally injected into the
   released exe by CI from a GitHub Actions secret (never visible in the repo).

So the public repository contains no address to scrape, yet a released build
can still know where to send reports.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import __version__, APP_NAME, paths

# CI may replace the empty string below with secrets.BUG_REPORT_EMAIL at build
# time (same mechanism as version stamping). Keep it blank in source.
_BUILD_STAMPED_EMAIL = ""


def resolve_bug_email(settings=None) -> str:
    """The configured bug-report recipient, or '' if none is set anywhere."""
    if settings is not None:
        v = (getattr(settings, "bug_report_email", "") or "").strip()
        if v:
            return v
    env = (os.environ.get("DCMTOOLKIT_BUG_EMAIL", "") or "").strip()
    if env:
        return env
    return _BUILD_STAMPED_EMAIL.strip()


def default_subject() -> str:
    return f"DICOM Toolkit bug report (v{__version__})"


# --- PHI scrubbing ---------------------------------------------------------
# Heuristics to keep patient identifiers out of a diagnostics bundle. Not a
# guarantee - the bundle is also shown to the user to review before sending.
_PN_RE = re.compile(r"[A-Za-z][\w'-]*\^[\w'^ -]+")          # DICOM PN (Last^First)
_NUM_RE = re.compile(r"(?<![\w.])\d{5,}(?![\w.])")          # long id/accession runs


def scrub(text: str) -> str:
    """Redact likely PHI (PN-style names, long numeric IDs) from log text."""
    text = _PN_RE.sub("[NAME]", text)
    text = _NUM_RE.sub("[NUM]", text)
    return text


def environment_info(settings=None) -> str:
    """Non-PHI environment summary for the report."""
    lines = [
        f"{APP_NAME} version: {__version__}",
        f"Frozen (exe): {paths.is_frozen()}",
        f"OS: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
    ]
    for mod in ("pydicom", "pynetdicom", "customtkinter", "numpy", "PIL"):
        try:
            m = __import__(mod)
            lines.append(f"{mod}: {getattr(m, '__version__', '?')}")
        except Exception:  # noqa: BLE001
            lines.append(f"{mod}: (not importable)")
    if settings is not None:
        lines.append(f"My AE Title: {getattr(settings, 'my_aetitle', '')}")
        try:
            n = len(settings.last_destinations)
        except Exception:  # noqa: BLE001
            n = "?"
        lines.append(f"Remembered destinations: {n}")
    lines.append(f"Data dir: {paths.data_dir()}")
    return "\n".join(lines)


def _log_tail(max_lines: int = 400) -> str:
    """The tail of the central log, scrubbed of likely PHI."""
    log = paths.log_dir() / "dcmtoolkit.log"
    if not log.exists():
        return "(no log file)"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read log: {exc})"
    return scrub("\n".join(lines[-max_lines:]))


def build_report(description: str, steps: str, *, include_log: bool = True,
                 settings=None) -> Path:
    """Write a diagnostics zip to the reports folder and return its path.

    Contents are non-image and PHI-scrubbed: a summary text file and (opt) the
    scrubbed tail of the activity log. No pixel data, no destinations endpoints.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zpath = paths.reports_dir() / f"bug_report_{stamp}.zip"
    summary = (
        f"{APP_NAME} bug report\n"
        f"Created: {datetime.now().isoformat(timespec='seconds')}\n"
        f"{'=' * 50}\n\n"
        f"WHAT HAPPENED\n{description.strip() or '(none given)'}\n\n"
        f"STEPS TO REPRODUCE\n{steps.strip() or '(none given)'}\n\n"
        f"{'=' * 50}\nENVIRONMENT\n{environment_info(settings)}\n"
    )
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bug_report.txt", summary)
        if include_log:
            z.writestr("activity_log_tail.txt", _log_tail())
    return zpath


def mailto_url(to_addr: str, subject: str, body: str) -> str:
    """Build a mailto: URL with pre-filled subject and body."""
    q = f"subject={quote(subject)}&body={quote(body)}"
    return f"mailto:{to_addr}?{q}" if to_addr else f"mailto:?{q}"


def report_body(zip_path: Path, settings=None) -> str:
    """Pre-filled email body (reminds the reporter to attach the zip)."""
    return (
        "Please describe anything else here.\n\n"
        f"IMPORTANT: attach the diagnostics file:\n{zip_path}\n"
        "(It contains only app/version/OS info and a PHI-scrubbed activity log "
        "- no images.)\n\n"
        f"{'-' * 40}\n{environment_info(settings)}\n"
    )


def inline_body(description: str, settings=None, *, log_lines: int = 60) -> str:
    """Self-contained email body: the user's description, the environment
    summary, and a short PHI-scrubbed log tail inlined - so there is nothing to
    attach. Capped so it fits in a mailto: without being truncated by the mail
    client."""
    return (
        f"{description.strip() or '(no description given)'}\n\n"
        f"{'-' * 40}\nDIAGNOSTICS (auto-added; no images, PHI-scrubbed)\n"
        f"{'-' * 40}\n{environment_info(settings)}\n\n"
        f"Recent activity log (last {log_lines} lines):\n"
        f"{_log_tail(log_lines)}\n"
    )
