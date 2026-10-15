"""Shared pytest fixtures: synthetic DICOM builders and throwaway mock SCPs.

Everything here is synthetic - no patient data, no network beyond localhost.
"""

from __future__ import annotations

import contextlib

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (generate_uid, ExplicitVRLittleEndian, CTImageStorage)

ENH_CT = "1.2.840.10008.5.1.4.1.1.2.1"
CLASSIC_CT = "1.2.840.10008.5.1.4.1.1.2"


def _item(**kw) -> Dataset:
    d = Dataset()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def make_ct(sop_instance=None, **over) -> Dataset:
    """A minimal valid single-frame CT image dataset (uncompressed)."""
    ds = Dataset()
    ds.PatientName = over.get("PatientName", "DOE^JANE")
    ds.PatientID = over.get("PatientID", "MRN1")
    ds.PatientBirthDate = over.get("PatientBirthDate", "19800101")
    ds.PatientSex = over.get("PatientSex", "F")
    ds.StudyInstanceUID = over.get("StudyInstanceUID", generate_uid())
    ds.SeriesInstanceUID = over.get("SeriesInstanceUID", generate_uid())
    ds.StudyDate = over.get("StudyDate", "20200101")
    ds.AccessionNumber = over.get("AccessionNumber", "ACC1")
    ds.Modality = over.get("Modality", "CT")
    ds.SOPClassUID = over.get("SOPClassUID", CTImageStorage)
    ds.SOPInstanceUID = sop_instance or generate_uid()
    ds.Rows = 4
    ds.Columns = 4
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = bytes(4 * 4 * 2)
    for k, v in over.get("extra", {}).items():
        setattr(ds, k, v)
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def make_enhanced_ct(n_frames: int = 3) -> Dataset:
    """A small Enhanced CT with shared + per-frame functional groups."""
    ds = make_ct(SOPClassUID=ENH_CT)
    ds.Rows = 2
    ds.Columns = 2
    ds.PixelData = bytes(n_frames * 2 * 2 * 2)
    ds.NumberOfFrames = n_frames
    ds.FrameOfReferenceUID = generate_uid()
    shared = Dataset()
    shared.PixelMeasuresSequence = [_item(PixelSpacing=[0.5, 0.5],
                                          SliceThickness=1.0)]
    shared.PlaneOrientationSequence = [
        _item(ImageOrientationPatient=[1, 0, 0, 0, 1, 0])]
    shared.PixelValueTransformationSequence = [
        _item(RescaleIntercept=-1024, RescaleSlope=1, RescaleType="HU")]
    shared.CTXRayDetailsSequence = [_item(KVP=120.0)]            # standard tag
    shared.CTExposureSequence = [_item(ExposureInmAs=55.0)]      # enhanced-only
    shared.CTImageFrameTypeSequence = [
        _item(FrameType=["ORIGINAL", "PRIMARY", "AXIAL", "NONE"])]
    ds.SharedFunctionalGroupsSequence = [shared]
    ds.PerFrameFunctionalGroupsSequence = [
        _item(PlanePositionSequence=[_item(ImagePositionPatient=[0, 0, i])],
              FrameContentSequence=[_item(InStackPositionNumber=i + 1)])
        for i in range(n_frames)]
    return ds


def save(ds: Dataset, path) -> str:
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


@pytest.fixture
def ct_factory():
    return make_ct


@pytest.fixture
def enhanced_ct_factory():
    return make_enhanced_ct


@contextlib.contextmanager
def storage_scp(contexts=None, handler=None):
    """A throwaway local Storage SCP on an ephemeral port. Yields a Node."""
    from pynetdicom import AE, evt
    from pynetdicom.presentation import AllStoragePresentationContexts
    from dcmtoolkit.model import Node

    def _store(event):
        return 0x0000

    ae = AE(ae_title="TESTSCP")
    for cx in (contexts or AllStoragePresentationContexts):
        ae.add_supported_context(cx.abstract_syntax)
    server = ae.start_server(
        ("127.0.0.1", 0), block=False,
        evt_handlers=[(evt.EVT_C_STORE, handler or _store)])
    try:
        port = server.server_address[1]
        yield Node(name="test", aetitle="TESTSCP", host="127.0.0.1",
                   port=port, timeout=20)
    finally:
        server.shutdown()
