# DICOM Toolkit

A modern, unified replacement for the legacy `DCM*` executables — every old tool
is a tab inside **one** double-click application (`DICOMToolkit.exe`), built on
the maintained [pydicom](https://pydicom.github.io/) /
[pynetdicom](https://pydicom.github.io/pynetdicom/) libraries. It runs **fully
offline**: no data ever leaves the machine except the DICOM associations *you*
initiate to destinations *you* configure.

## What replaced what

| Legacy tool | Now in the app as |
|-------------|-------------------|
| `DCMCEcho.exe` | **C-Echo** tab |
| `DCMSend.exe` | **Send (C-Store)** tab |
| `DCMQueryMove.exe` / `DCMDropMove.exe` | **Query / Move** tab |
| `DCMModify.exe` | **Modify Header** tab |
| `DCMTagLister.exe` | **Tag Lister** tab |
| `DCMSplitMF.exe` | **Split Multiframe** tab |
| `DCMFolderDumper.exe` / `DumpExamData.exe` | **Folder Dump** tab |
| `DCMStoreService` | **Store Receiver** tab (in-app *or* a Windows service) |
| `JDICOM\ModalitySCU` | **Modality SCU** tab (Worklist + MPPS + exam) |
| `custom_code_*.cs` | Anonymize / morph / pixel-blank, all built in |

> `SmartHL7.Sender` is HL7, not DICOM, and is **not** part of this app.

## Beyond the legacy tools

| Tab | What it does |
|-----|--------------|
| **Echo All (health)** | Ping every saved destination; sortable table (click a column), OK/Fail filter, latency. |
| **Retrieve (C-Get)** | Pull studies to a local folder over one association — no need to register your AE/port on the remote (firewall-friendly). |
| **Storage Commit** | Ask an archive to confirm it stored specific objects (N-ACTION + N-EVENT-REPORT). |
| **Patient / Study / Series Info** | Edit demographics across a loaded study in labelled fields, with live DICOM VR validation. |
| **Enhanced → Classic** | Convert Enhanced CT/MR/PET multi-frame objects to classic single-frame DICOM (functional groups flattened). |
| **Preview** | Render an object's pixels to a thumbnail (grayscale/color, multi-frame) for a visual spot-check. |
| **QA / Consistency** | Scan a folder for problems (see below). |
| **De-identify Files** | DICOM PS3.15 Basic Profile + the receiver's anonymize/morph/pixel-blank engine. |
| **Modality SCU** | Query the Modality Worklist, then perform an exam: MPPS In Progress → stamp & send images → MPPS Completed. |

Most tools accept **folders and `.zip` archives** (dropped or via the buttons) —
zips are auto-extracted to a temp folder and queued, no manual unzip. Large
transfers run in parallel with a progress bar + Cancel.

## Validation (QA / Consistency)

Load a folder or zip and scan. On top of the built-in tag checks
(incongruent / abnormal / missing / duplicate / folder-mismatch), three opt-in
checks cover the axes file inspection alone can't:

- **IOD conformance** — does each object match the DICOM standard's required
  modules and Type-1/1C/2/2C attributes for its SOP class (via a copy of the
  standard bundled in the exe).
- **C-STORE send-ability** — round-trips each object through a built-in standard
  Storage SCP to catch transfer-syntax / SOP-class problems that would stop a
  real send (e.g. private/unknown SOP classes).
- **Render** — decodes the pixel data and exercises the display pipeline
  (modality + VOI LUT), flagging anything that won't decode. JPEG / JPEG2000 /
  JPEG-LS decode offline via bundled decoders.

Findings are colour-coded, filterable by category, and exportable to CSV.
Everything runs **offline** — no image data leaves the machine.

## De-identification

The **De-identify Files** tool (same engine as the Store Receiver) offers a
one-click **DICOM PS3.15 Basic Confidentiality Profile**: it empties/removes the
confidentiality-profile attributes, removes private tags, replaces UIDs
consistently across the batch, and stamps the de-identification markers
(`PatientIdentityRemoved=YES`, `DeidentificationMethod`, code `113100`). It is
strict about site identifiers (institution name/address/department/station) by
default, with an optional **retain study dates** toggle for longitudinal use.
The legacy anonymize-by-file / morph / top-left pixel-blanking options remain.

> ⚠ The Basic Profile is an **attribute** profile — it does **not** remove
> burned-in pixel text. Use the Remove-Image options and/or the **Preview** /
> **QA → Render** checks to catch burned-in PHI.

## Running

- **Users:** double-click `dist\DICOMToolkit.exe`. No Python install required.
- **Developers:**
  ```
  py -3 -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\python main.py
  ```

## Sharing the exe internally

The whole app is the single `DICOMToolkit.exe` — nothing else needs to travel
with it. To share on an internal drive:

1. Copy **`DICOMToolkit.exe`** to the share (that one file is the whole app).
2. Tell recipients to **copy it to a local, writable folder** (e.g. their
   Desktop) before running — it creates its `DICOMToolkit-data\` beside itself.
3. On first launch Windows **SmartScreen** may warn (unsigned exe): *More info →
   Run anyway*. Some antivirus may need to allowlist a PyInstaller exe.
4. Each person adds their own destinations (Settings). To hand over a shared
   list, use **Settings → Export** and have them **Import** it — don't copy your
   raw `DICOMToolkit-data\` (it also holds your logs/reports).

## Configuration & data

Settings live in a portable `DICOMToolkit-data\` folder next to the exe (falls
back to `%APPDATA%\DICOMToolkit` if that location is read-only):

- `settings.json` — local AE title, appearance, window geometry, worker counts, TLS paths
- `destinations.json` — the address book (name, AE, host, port, **group**, optional **calling AE**)
- `receiver.json` — the Store Receiver config (shared with the Windows service)
- `logs\`, `reports\`, `exams\` — activity log, per-run reports, converted exams

> 🔒 **This folder is git-ignored and never committed.** It holds your site's
> real AE titles, hostnames, and ports — they stay on your machine only. The
> repository ships **no** real destinations.

### Destinations

Add them on the **Settings** tab (inline **+ Add**, **Import** CSV/JSON, or
**Import legacy folder** of old `DCM*.csv`), then **Save**. Two refinements:

- **Group** — *PACS / Storage* (default) or *Worklist / MPPS*. Storage tools
  show only storage destinations; the Modality SCU's worklist picker shows only
  worklist/MPPS servers. Destinations sort alphabetically.
- **Calling AE (optional)** — present a specific local AE title to *this*
  destination (e.g. a RIS that only answers a registered modality AE), without
  changing your app-wide My AE Title.

A template is provided at [`destinations.sample.csv`](destinations.sample.csv)
(uses the RFC 5737 documentation range, not real hosts).

## Store Receiver as a Windows service

```
DICOMToolkit.exe service install
DICOMToolkit.exe service start
DICOMToolkit.exe service stop
DICOMToolkit.exe service remove
DICOMToolkit.exe service run     # foreground, for testing
```

The service reads the same `receiver.json`, so what you test in-app is what the
service runs.

### Secure DICOM (TLS)

Each destination has a per-destination timeout and a TLS toggle; client TLS
material (CA trust file + optional client cert/key for mutual TLS) is configured
once under **Client TLS**. The Store Receiver can require TLS (and optionally
mutual TLS) on its listener. Certificate-chain verification against your CA is
enforced; hostname checking is off by design (PACS certs rarely match the IP).

## Offline & privacy

Everything runs on the machine. The DICOM standard used by the conformance
check (parsed to ~2.5 MB of JSON) and the compressed-pixel decoders
(JPEG/JPEG2000/JPEG-LS) are **bundled in the exe** — nothing is downloaded at
runtime, and no image data is ever transmitted except the DICOM associations you
start to destinations you configure.

## Building the exe

```
.venv\Scripts\pyinstaller DICOMToolkit.spec --noconfirm --clean
```

Produces `dist\DICOMToolkit.exe` (~40 MB, single file; includes the bundled
DICOM standard + pixel decoders).

## Updates (self-update)

The built exe can update itself from GitHub Releases:

- On launch it quietly checks the latest release; if a newer version exists, an
  **⬆ Update to vX.Y.Z** button appears in the status bar. There's also a
  **Check for updates** button in Settings.
- Clicking it downloads the new exe (SHA‑256 verified) and swaps it in, then
  reopens on the new version. **Your `DICOMToolkit-data\` (settings,
  destinations, logs) is left untouched** — it lives beside the exe, not inside
  it.
- Only HTTPS **GET** requests to GitHub are made — nothing is ever uploaded.
  Offline or firewalled machines simply skip the check (no errors) and keep
  their version.

> The downloaded exe is unsigned, so Windows SmartScreen may prompt again.

### Releasing a new version (maintainer)

Releases are driven by the **commit message** — no buttons, no manual tags.
Put the marker in a commit's **subject line** (the first line) on `main` and CI
([`.github/workflows/release.yml`](.github/workflows/release.yml)) bumps the
version, builds the exe on a Windows runner, and publishes it as a Release that
every installed app then offers via self-update:

```
git commit -m "fix rescale slope flattening [release]"        # patch -> vX.Y.(Z+1)
git commit -m "add stack splitting [release minor]"           # minor -> vX.(Y+1).0
git commit -m "new config format, breaking [release major]"   # major -> v(X+1).0.0
git push
```

- **No `[release]` marker → nothing is built.** You stay in control: ordinary
  commits never reach users; only a commit you mark cuts a release.
- The version number is computed automatically from the last release tag and the
  bump you asked for (default `patch`), stamped into the build, SHA‑256'd, and
  attached (`DICOMToolkit.exe` + `.sha256`) with auto‑generated notes.
- A manually pushed `vX.Y.Z` tag still triggers a build too (fallback).

## Testing

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The suite (`tests/`) covers the engines offline with synthetic data and
throwaway localhost SCPs — file/zip ops, Enhanced→Classic, the QA checks
(conformance / send-ability / render), de-identification, network round-trips
(echo / store / worklist / MPPS), and the model + import/export.

## Project layout

```
dcmtoolkit/
  paths.py, config.py, model.py, logging_setup.py, importer.py
  net/      scu.py (echo/send/find/move/get/commit), scp.py (receiver),
            mwl.py (worklist), mpps.py, selftest.py (C-STORE send-ability)
  store/    naming.py (folder formats), processing.py (anon/morph/PS3.15/pixel)
  tools/    fileops.py, dicomtags.py, qa.py, conformance.py, render.py,
            enhanced.py (Enhanced→Classic), modality.py (exam stamping)
  gui/      app.py + panels_*.py
  service.py
assets/dicom-standard/   bundled DICOM standard JSON (conformance)
tests/                   pytest suite
main.py                  entry point
DICOMToolkit.spec        PyInstaller build
```
