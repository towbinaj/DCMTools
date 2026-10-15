"""Render / pixel-decode check: confirm an object's pixels actually decode and
its display attributes form a usable image - what a viewer needs - fully
offline.

Complements IOD conformance (structure) and C-STORE send-ability (transfer):
this catches pixel data that won't decode, pixel attributes inconsistent with
the data, and broken grayscale display (modality/VOI LUT). Works the same for
ophthalmic OP/OPT as any other image. Compressed transfer syntaxes decode via
the bundled pylibjpeg (JPEG / JPEG2000 / JPEG-LS); RLE via pydicom.
"""

from __future__ import annotations

from pydicom.dataset import Dataset

try:
    from pydicom.pixels import apply_modality_lut, apply_voi_lut
except ImportError:  # older pydicom
    from pydicom.pixel_data_handlers.util import (  # type: ignore
        apply_modality_lut, apply_voi_lut)


def decoders() -> dict[str, bool]:
    """Availability of the common compressed-pixel decoders (for diagnostics)."""
    out: dict[str, bool] = {}
    try:
        from pydicom.pixels import get_decoder
        from pydicom.uid import (JPEG2000, JPEG2000Lossless, JPEGLSLossless,
                                 JPEGBaseline8Bit, RLELossless)
        for u in (JPEGBaseline8Bit, JPEG2000, JPEG2000Lossless, JPEGLSLossless,
                  RLELossless):
            try:
                out[u.name] = bool(get_decoder(u).is_available)
            except Exception:  # noqa: BLE001
                out[u.name] = False
    except Exception:  # noqa: BLE001
        pass
    return out


def render_errors(ds: Dataset) -> list[str]:
    """Return problems preventing the object from rendering (empty = OK).

    Objects with no image (SR, KO, PR, raw, ...) return [] - not a failure.
    Never raises; any internal error is reported as a finding instead.
    """
    if "PixelData" not in ds:
        return []
    try:
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        return [f"pixel decode failed: {str(exc).strip().replace(chr(10), ' ')[:180]}"]

    # Decoded size must match the declared geometry.
    try:
        rows = int(ds.Rows)
        cols = int(ds.Columns)
        frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
        spp = int(getattr(ds, "SamplesPerPixel", 1) or 1)
        expected = rows * cols * frames * spp
        if expected and int(arr.size) != expected:
            return [f"decoded {int(arr.size)} samples, expected {expected} "
                    f"({frames}f x {rows}x{cols} x {spp})"]
    except Exception as exc:  # noqa: BLE001
        return [f"pixel geometry error: {str(exc).strip()[:140]}"]

    # Grayscale display pipeline (modality + VOI LUT / windowing).
    photometric = str(getattr(ds, "PhotometricInterpretation", "")).upper()
    if photometric.startswith("MONOCHROME"):
        try:
            frame = arr[0] if (arr.ndim >= 3 and frames > 1) else arr
            apply_voi_lut(apply_modality_lut(frame, ds), ds)
        except Exception as exc:  # noqa: BLE001
            return [f"display LUT/windowing failed: {str(exc).strip()[:150]}"]
    return []
