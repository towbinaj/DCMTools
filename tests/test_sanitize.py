"""Structural sanitize: carrier detection + stripping (synthetic data only)."""

from __future__ import annotations

from pydicom.dataset import Dataset

from dcmtoolkit.tools import sanitize
from dcmtoolkit.tools.sanitize import SanitizeConfig, sanitize_dataset, scan_dataset
from dcmtoolkit.store.processing import Processor, ReceiverConfig

CT_SOP = "1.2.840.10008.5.1.4.1.1.2"
ENCAP_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _carrier_ds(sop=CT_SOP):
    ds = Dataset()
    ds.SOPClassUID = sop
    ds.add_new(0x00880200, "SQ", [])             # Icon Image Sequence
    ds.add_new(0x00420011, "OB", b"\x00\x01")    # Encapsulated Document stream
    ds.add_new(0x60003000, "OW", b"\x00\x00")    # Overlay Data (group 6000)
    ds.add_new(0x60023000, "OW", b"\x00\x00")    # Overlay Data (group 6002)
    ds.add_new(0x50003000, "OW", b"\x00\x00")    # Curve Data (group 5000)
    ds.PixelData = b"\x01\x02\x03\x04"           # primary pixels (must survive)
    return ds


def test_scan_detects_all_carriers():
    rep = scan_dataset(_carrier_ds())
    assert rep.icon_sequences == 1
    assert rep.overlay_groups == 2        # 6000 and 6002 counted distinctly
    assert rep.curve_groups == 1
    assert rep.encapsulated_docs == 1
    assert rep.is_encapsulated_object is False   # CT SOP, not a document class
    assert rep.any_found is True


def test_sanitize_strips_carriers_keeps_pixels():
    ds = _carrier_ds()
    sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert 0x00880200 not in ds
    assert 0x00420011 not in ds
    assert 0x60003000 not in ds and 0x60023000 not in ds
    assert 0x50003000 not in ds
    assert "PixelData" in ds and ds.PixelData == b"\x01\x02\x03\x04"


def test_disabled_detects_but_strips_nothing():
    ds = _carrier_ds()
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=False))
    assert rep.any_found is True           # still reports
    assert 0x00880200 in ds and 0x60003000 in ds   # nothing removed


def test_none_config_detects_but_strips_nothing():
    ds = _carrier_ds()
    rep = sanitize_dataset(ds, None)
    assert rep.any_found is True
    assert 0x00420011 in ds


def test_sub_flag_off_keeps_that_carrier():
    ds = _carrier_ds()
    sanitize_dataset(ds, SanitizeConfig(enabled=True, strip_overlays=False))
    assert 0x60003000 in ds                # overlays kept
    assert 0x00880200 not in ds            # icon still stripped


def test_encapsulated_sop_class_flagged():
    rep = scan_dataset(_carrier_ds(sop=ENCAP_PDF_SOP))
    assert rep.is_encapsulated_object is True
    assert "encapsulated document" in " ".join(rep.findings())


def test_preamble_payload_detected():
    ds = Dataset()
    ds.SOPClassUID = CT_SOP
    ds.preamble = b"MZ" + b"\x90" * 126
    rep = scan_dataset(ds)
    assert rep.preamble_payload is True
    assert "MZ" in rep.preamble_signature
    # clean preamble -> no payload
    ds.preamble = b"\x00" * 128
    assert scan_dataset(ds).preamble_payload is False


def test_clean_object_has_no_findings():
    ds = Dataset()
    ds.SOPClassUID = CT_SOP
    ds.preamble = b"\x00" * 128
    ds.PixelData = b"\x01\x02"
    rep = scan_dataset(ds)
    assert rep.any_found is False
    assert rep.findings() == []


def test_pipeline_strips_when_enabled():
    # Store Receiver / De-identify share Processor.process(); the toggle strips.
    ds = _carrier_ds()
    Processor(ReceiverConfig(sanitize_enabled=True)).process(ds)
    assert 0x00880200 not in ds and 0x60003000 not in ds and 0x00420011 not in ds


def test_pipeline_keeps_carriers_when_disabled():
    ds = _carrier_ds()
    Processor(ReceiverConfig(sanitize_enabled=False)).process(ds)
    assert 0x00880200 in ds and 0x60003000 in ds
