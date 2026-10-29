"""Download the pinned OHIF viewer build into assets/ohif-viewer/.

OHIF publishes its production build in the ``@ohif/app`` npm package (``dist/``),
so no Node toolchain is needed: fetch the tarball, check it against the pinned
SHA-512, and unpack ``dist/``. Run once for development, and in CI before
PyInstaller (the spec bundles the folder when it exists).

    python scripts/fetch_ohif.py

Two optional parts are left out to keep the exe small (~150 MB saved):
``ort/`` (ONNX runtime for AI-assisted segmentation) and
``dicom-microscopy-viewer/`` (whole-slide microscopy mode).
"""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "3.13.13"
URL = f"https://registry.npmjs.org/@ohif/app/-/app-{VERSION}.tgz"
SHA512 = ("qqrHXYSVnLYy1qrJGVAkLvZEFnV39qUv21arpNTbAcdaxMIUIctf9bN6gad2QKlZ"
          "uamPLvPb8RSCmjqn92T23g==")
SKIP = ("ort/", "dicom-microscopy-viewer/")

DEST = Path(__file__).resolve().parent.parent / "assets" / "ohif-viewer"


def main() -> int:
    stamp = DEST / ".version"
    if stamp.is_file() and stamp.read_text().strip() == VERSION:
        print(f"OHIF {VERSION} already in {DEST}")
        return 0
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL, timeout=300) as r:
        blob = r.read()
    digest = base64.b64encode(hashlib.sha512(blob).digest()).decode()
    if digest != SHA512:
        print("SHA-512 mismatch - refusing to unpack.", file=sys.stderr)
        return 1
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    n = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            rel = m.name.removeprefix("package/")
            if rel == "LICENSE":
                rel = "dist/LICENSE"
            if not rel.startswith("dist/"):
                continue
            rel = rel.removeprefix("dist/")
            if rel.startswith(SKIP) or rel.endswith(".map"):
                continue
            out = (DEST / rel).resolve()
            if DEST.resolve() not in out.parents:
                continue    # never write outside DEST
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tar.extractfile(m).read())
            n += 1
    stamp.write_text(VERSION)
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"Unpacked {n} files ({size / 1e6:.0f} MB) to {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
