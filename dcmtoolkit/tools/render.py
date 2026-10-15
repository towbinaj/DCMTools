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


def frame_count(ds: Dataset) -> int:
    try:
        return max(1, int(getattr(ds, "NumberOfFrames", 1) or 1))
    except (TypeError, ValueError):
        return 1


def render_image(ds: Dataset, frame: int = 0):
    """Return a PIL.Image for the given frame (for a visual spot-check).

    Handles grayscale (MONOCHROME1/2, via modality+VOI LUT and 8-bit
    normalization) and color (RGB / YBR converted to RGB). Raises if there is
    no pixel data or it cannot be decoded.
    """
    import numpy as np
    from PIL import Image

    if "PixelData" not in ds:
        raise ValueError("no pixel data (nothing to display)")
    arr = ds.pixel_array
    frames = frame_count(ds)
    if frames > 1 and arr.ndim >= 3 and arr.shape[0] == frames:
        arr = arr[max(0, min(frame, frames - 1))]

    photometric = str(getattr(ds, "PhotometricInterpretation", "")).upper()
    if photometric.startswith("MONOCHROME") or arr.ndim == 2:
        out = apply_voi_lut(apply_modality_lut(arr, ds), ds).astype("float64")
        lo, hi = float(np.nanmin(out)), float(np.nanmax(out))
        out = (out - lo) / (hi - lo) * 255.0 if hi > lo else out * 0
        if photometric == "MONOCHROME1":
            out = 255.0 - out
        return Image.fromarray(out.astype("uint8"), mode="L")

    # Color: convert YBR -> RGB if needed, ensure 8-bit.
    if photometric.startswith("YBR"):
        try:
            from pydicom.pixels import convert_color_space
        except ImportError:
            from pydicom.pixel_data_handlers.util import convert_color_space
        arr = convert_color_space(arr, photometric, "RGB")
    if arr.dtype != np.uint8:
        a = arr.astype("float64")
        lo, hi = float(a.min()), float(a.max())
        arr = ((a - lo) / (hi - lo) * 255.0 if hi > lo else a * 0).astype("uint8")
    return Image.fromarray(arr[..., :3], mode="RGB")


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
