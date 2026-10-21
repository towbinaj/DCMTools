# PyInstaller spec for the DICOM Toolkit.
# Build:  pyinstaller DICOMToolkit.spec --noconfirm
# Output: dist/DICOMToolkit.exe  (single-file, double-click)

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas = [("assets/icon.ico", "assets")]
# Bundle the DICOM standard (parsed to JSON) so IOD conformance validation
# works fully offline - preserve the assets/dicom-standard/<edition>/json layout.
import os
for _root, _dirs, _files in os.walk(os.path.join("assets", "dicom-standard")):
    for _f in _files:
        _src = os.path.join(_root, _f)
        datas.append((_src, _root))
binaries = []
hiddenimports = []

# CustomTkinter ships theme JSON + assets; tkinterdnd2 ships the tkdnd binaries.
# Both must travel with the exe.
for pkg in ("customtkinter", "tkinterdnd2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pynetdicom registers SOP classes dynamically; pull in all submodules.
hiddenimports += collect_submodules("pynetdicom")
hiddenimports += collect_submodules("pydicom")
# dicom-validator is imported lazily for conformance checks; pull it all in.
hiddenimports += collect_submodules("dicom_validator")
# dicom-anonymizer (PS3.15 Basic Profile) ships action-table data files.
_da, _db, _dh = collect_all("dicomanonymizer")
datas += _da
binaries += _db
hiddenimports += _dh

# Compressed-pixel decoders (render check). pylibjpeg discovers its plugins via
# entry-point metadata, so collect the packages AND their metadata, plus the
# compiled plugin modules and pydicom's pixel handlers.
for pkg in ("pylibjpeg", "pylibjpeg_libjpeg", "pylibjpeg_openjpeg"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for dist in ("pylibjpeg", "pylibjpeg-libjpeg", "pylibjpeg-openjpeg"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass
hiddenimports += ["openjpeg", "libjpeg"]
hiddenimports += collect_submodules("pydicom.pixels")
hiddenimports += collect_submodules("pydicom.pixel_data_handlers")

# pywin32 pieces needed for the Windows service path.
hiddenimports += [
    "win32timezone", "win32service", "win32serviceutil",
    "win32event", "servicemanager",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim dead weight to shrink the onefile bundle (faster extract on launch
    # and cleanup on exit): we never handle AVIF/HEIF images - all pixel
    # decoding is pylibjpeg (JPEG/JPEG2000/JPEG-LS) + pydicom RLE - so Pillow's
    # ~8 MB AVIF plugin is pure bloat.
    excludes=["pytest", "tkinter.test", "test",
              "PIL._avif", "PIL.AvifImagePlugin"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DICOMToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed GUI; no console flashes on double-click
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
