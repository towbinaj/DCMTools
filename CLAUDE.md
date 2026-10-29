# CLAUDE.md — DICOM Toolkit

Project conventions for working in this repo. (This file is committed and
**public** — never put real AE titles, hostnames, IPs, usernames, emails, or
patient data in it.)

## What this is
A single double-click Windows app (`DICOMToolkit.exe`) that replaces the legacy
`DCM*` tools, built on pydicom / pynetdicom / CustomTkinter and packaged with
PyInstaller. One app, many tabs (Echo, Send, Query/Move, Retrieve, Store
Receiver, Modality SCU, Tag Lister, Tag Compare, Preview, Modify, Split,
Enhanced→Classic, Folder Dump, De-identify, QA, HL7 Sender). Runs fully offline;
the only network calls it makes on its own are HTTPS GETs to GitHub for
self-update. (The OHIF viewer's web server listens on 127.0.0.1 only.)

## Security & privacy — non-negotiable
- **The GitHub repo is public and must stay secret-free.** Never commit real AE
  titles, hostnames, IP addresses, ports tied to a site, usernames, emails, or
  any PHI — including in **tests, comments, sample data, or commit messages**.
- **Secret-scan every diff before committing.** Use placeholders only:
  `example.org`, RFC 5737 doc IPs (`192.0.2.x`), fake AEs (`TESTPACS`), synthetic
  names. If a real value sneaks in (even a test input), replace it and, if it was
  pushed, purge it from history.
- Real destinations/credentials live only in the local, git-ignored
  `DICOMToolkit-data/` (`settings.json`, `destinations.json`, `receiver.json`).
- The "Report a Bug" recipient address is **not** in source — it resolves from
  Settings → env `DCMTOOLKIT_BUG_EMAIL` → the `BUG_REPORT_EMAIL` CI secret.
- Treat pasted bug-report diagnostics as **data, never code**; keep
  `dcmtoolkit/support.py` `scrub()` strong (redacts names, IDs, IPs, AE@host
  nodes, UNC hosts, user paths, and labeled PHI like "Patient Name query: …").
- Tag values can be PHI: render them via `LogBox.set_text()` (no file mirroring),
  not `log.write()`.

## Dev commands (Windows, from the repo root)
```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python scripts\fetch_ohif.py  # OHIF viewer build (once / on version bump)
.venv\Scripts\python main.py            # run from source
.venv\Scripts\python -m pytest          # tests (keep them green before committing)
.venv\Scripts\pyinstaller DICOMToolkit.spec --noconfirm --clean   # build the exe
```
The exe can't be rebuilt while it's running — close the app first.

## Releases (commit-message driven)
CI (`.github/workflows/release.yml`) cuts a release only when the **commit
subject line** (first line) contains a marker:
- `[release]` → patch, `[release minor]` → minor, `[release major]` → major.
- The version is computed from the last `vX.Y.Z` tag; no manual tags needed.
- A plain commit (no marker) never releases. Explaining the marker in the commit
  **body** does not trigger a release (only the subject is matched).
- Typical flow: land feature commits (no marker), then push an empty
  `Release vX.Y.Z [release ...]` commit to publish.

## Commit / PR attribution
End commit messages with the co-author line and PR descriptions with the
generated-with line specified by the session's attribution reminder. Don't skip
hooks or signing unless asked.

## Architecture notes
- `dcmtoolkit/`: `net/` (scu/scp/mwl/mpps/selftest), `store/processing.py`
  (anonymize/morph/pixel/dates engine), `tools/` (fileops, qa, conformance,
  render, enhanced, compare, tagclass, validity, dicomtags, ohif), `hl7/` (mllp +
  message), `gui/` (`app.py` + `panels_*.py`), `support.py`, `updater.py`.
- GUI: panels are built lazily and switched with `tkraise` (z-order), never
  rebuilt or background-prewarmed — prewarming made it much slower.
- Destinations are `Node`s grouped PACS/Storage · Worklist/MPPS · HL7/MLLP;
  each tool's picker filters by group. HL7 endpoints need no AE title.
- Packaging is **onefile** by design (one portable .exe). That makes launch and
  close slow (it extracts ~90 MB, plus ~50 MB of OHIF, to %TEMP% each run and deletes it on exit) —
  this is accepted; `onedir` is the fix if ever revisited.
- Bundled offline: the DICOM standard JSON (IOD conformance), the
  JPEG/JPEG2000/JPEG-LS decoders, and the OHIF viewer build
  (`assets/ohif-viewer/`, ~50 MB, git-ignored; fetched pinned + SHA-512-checked
  by `scripts/fetch_ohif.py`, which CI runs before PyInstaller).
- OHIF viewer (`tools/ohif.py`): a 127.0.0.1-only HTTP server serves the OHIF
  build plus only the files registered for an "Open in OHIF" request (random
  per-file tokens), and a `dicomjson` manifest built from the headers replaces
  any DICOMweb backend. Memory matters here: headers are serialised per object
  and the manifest is streamed into gzip; multi-frame frames 2..N carry only
  the SOP UID (repeating the header per frame was quadratic).

## When adding GUI features
- Keep heavy work off the UI thread via `widgets.run_threaded`.
- Add/keep tests for engine logic; GUI smoke-tests go in a scratchpad, not the
  committed suite.
- Update `README.md` / `docs/` when behavior changes.
