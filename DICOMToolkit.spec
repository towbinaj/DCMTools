# PyInstaller spec for the DICOM Toolkit.
# Build:  pyinstaller DICOMToolkit.spec --noconfirm
# Output: dist/DICOMToolkit.exe  (single-file, double-click)

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("assets/icon.ico", "assets")]
binaries = []
hiddenimports = []

# CustomTkinter ships theme JSON + assets that must travel with the exe.
for pkg in ("customtkinter",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pynetdicom registers SOP classes dynamically; pull in all submodules.
hiddenimports += collect_submodules("pynetdicom")
hiddenimports += collect_submodules("pydicom")

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
    excludes=["pytest", "tkinter.test", "test"],
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
