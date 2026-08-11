# DICOM Toolkit

A modern, unified replacement for the legacy `DCM*` executables. Every old tool
is now a tab inside **one** double-click application (`DICOMToolkit.exe`), built
on the maintained [pydicom](https://pydicom.github.io/) /
[pynetdicom](https://pydicom.github.io/pynetdicom/) libraries.

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
| `DCMStoreService` | **Store Receiver** tab (runs in-app *or* as a Windows service) |
| `custom_code_*.cs` (C# compiled at runtime) | Anonymize / Morph config + pixel-blanking, all built in |

> `SmartHL7.Sender` is HL7, not DICOM, and is **not** part of this app (separate track if wanted).

## Running

- **Users:** double-click `dist/DICOMToolkit.exe`. No Python install required.
- **Developers:**
  ```
  py -3 -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\python main.py
  ```

## Configuration & data

Settings live in a portable `DICOMToolkit-data\` folder next to the exe
(falls back to `%APPDATA%\DICOMToolkit` if that location is read-only):

- `settings.json` — your local AE title + appearance
- `destinations.json` — the PACS/VNA/etc. address book (used by every tab)
- `receiver.json` — the Store Receiver config (shared with the Windows service)
- `logs\dcmtoolkit.log` — rotating activity log

> 🔒 **This folder is git-ignored and is never committed.** `destinations.json`
> and friends hold your site's real AE titles, hostnames, and ports — they stay
> on your machine only. The repository ships **no** real destinations.

## Adding your destinations (first run)

The app starts with an empty address book. Populate it from the **Settings** tab
in any of these ways:

1. **+ Add** — type a single destination (name, AE title, host, port) inline.
2. **Import file…** — load a `.csv` or `.json` file of destinations.
3. **Import legacy folder…** — point at a folder containing old
   `DCMSend.csv` / `DCMQueryMove.csv` / `DCMDropMove.csv` files.
4. **Export…** — save your current list to `.csv` or `.json` (for backup or to
   share with a teammate — remember this file contains real addresses).

Then click **Save**. A template is provided at
[`destinations.sample.csv`](destinations.sample.csv):

```csv
name,aetitle,host,port
Example PACS,EXAMPLE_PACS,192.0.2.10,104
Example VNA,EXAMPLE_VNA,192.0.2.20,11112
```

(The importer also accepts the legacy `MoveSource,name,aetitle,host,port` row
format, so old exported CSVs work as-is.)

## Store Receiver as a Windows service

The receiver can run headless and always-on, like the old `DCMStoreService`.
Configure it on the **Store Receiver** tab, click **Save config**, then use the
service buttons (they prompt for UAC elevation), or from an elevated console:

```
DICOMToolkit.exe service install
DICOMToolkit.exe service start
DICOMToolkit.exe service stop
DICOMToolkit.exe service remove
DICOMToolkit.exe service run     # run in the foreground for testing
```

The service reads the same `receiver.json`, so what you test in-app is exactly
what the service runs. Supported de-identification (all from the old Setup
Guide): remove private tags, remove groups/tags, anonymize-by-file (REMOVE /
BLANK / replace) with consistent UID remapping, calculated dates, top/left pixel
blanking with modality filters, and tag morphing.

## Building the exe

```
.venv\Scripts\pyinstaller DICOMToolkit.spec --noconfirm --clean
```

Produces `dist\DICOMToolkit.exe` (~34 MB, single file).

## Project layout

```
dcmtoolkit/
  paths.py, config.py, model.py, logging_setup.py, importer.py
  net/      scu.py (echo/send/find/move), scp.py (receiver)
  store/    naming.py (folder formats), processing.py (anon/morph/pixel)
  tools/    fileops.py (tag list / modify / split / dump)
  gui/      app.py + panels_*.py
  service.py
main.py                # entry point
DICOMToolkit.spec      # PyInstaller build
```
