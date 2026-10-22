"""Classify a DICOM tag by the information-model entity it describes.

This is a pragmatic grouping for the Tag Lister's class filter - Patient /
Study / Series / Equipment / Frame of Ref / Image / Private / Other - not a
formal IOD module mapping. Groups 0008 and 0020 are mixed across entities, so
the well-known attributes there are curated explicitly and everything else
falls back to a per-group default.
"""

from __future__ import annotations

import re

PATIENT = "Patient"
STUDY = "Study"
SERIES = "Series"
EQUIPMENT = "Equipment"
FRAME = "Frame of Ref"
IMAGE = "Image"
PRIVATE = "Private"
OTHER = "Other"

CLASSES = [PATIENT, STUDY, SERIES, EQUIPMENT, FRAME, IMAGE, PRIVATE, OTHER]

# Curated (group, element) -> class for attributes whose group is shared across
# entities (mostly 0008 and 0020), so they don't fall into the group default.
_CURATED = {
    # Study
    (0x0020, 0x000D): STUDY,   # StudyInstanceUID
    (0x0020, 0x0010): STUDY,   # StudyID
    (0x0008, 0x0020): STUDY,   # StudyDate
    (0x0008, 0x0030): STUDY,   # StudyTime
    (0x0008, 0x0050): STUDY,   # AccessionNumber
    (0x0008, 0x0051): STUDY,   # IssuerOfAccessionNumberSequence
    (0x0008, 0x0090): STUDY,   # ReferringPhysicianName
    (0x0008, 0x1030): STUDY,   # StudyDescription
    (0x0008, 0x1032): STUDY,   # ProcedureCodeSequence
    (0x0008, 0x1060): STUDY,   # NameOfPhysiciansReadingStudy
    (0x0008, 0x1110): STUDY,   # ReferencedStudySequence
    # Series
    (0x0020, 0x000E): SERIES,  # SeriesInstanceUID
    (0x0020, 0x0011): SERIES,  # SeriesNumber
    (0x0020, 0x0060): SERIES,  # Laterality
    (0x0008, 0x0060): SERIES,  # Modality
    (0x0008, 0x0021): SERIES,  # SeriesDate
    (0x0008, 0x0031): SERIES,  # SeriesTime
    (0x0008, 0x103E): SERIES,  # SeriesDescription
    (0x0008, 0x1050): SERIES,  # PerformingPhysicianName
    (0x0018, 0x0015): SERIES,  # BodyPartExamined
    (0x0018, 0x1030): SERIES,  # ProtocolName
    (0x0018, 0x5100): SERIES,  # PatientPosition
    # Equipment
    (0x0008, 0x0070): EQUIPMENT,  # Manufacturer
    (0x0008, 0x0080): EQUIPMENT,  # InstitutionName
    (0x0008, 0x0081): EQUIPMENT,  # InstitutionAddress
    (0x0008, 0x1010): EQUIPMENT,  # StationName
    (0x0008, 0x1040): EQUIPMENT,  # InstitutionalDepartmentName
    (0x0008, 0x1090): EQUIPMENT,  # ManufacturerModelName
    (0x0008, 0x1070): EQUIPMENT,  # OperatorsName
    (0x0018, 0x1000): EQUIPMENT,  # DeviceSerialNumber
    (0x0018, 0x1020): EQUIPMENT,  # SoftwareVersions
    (0x0018, 0x1200): EQUIPMENT,  # DateOfLastCalibration
    # Frame of Reference
    (0x0020, 0x0052): FRAME,   # FrameOfReferenceUID
    (0x0020, 0x1040): FRAME,   # PositionReferenceIndicator
    # Image / Instance
    (0x0008, 0x0016): IMAGE,   # SOPClassUID
    (0x0008, 0x0018): IMAGE,   # SOPInstanceUID
    (0x0008, 0x0008): IMAGE,   # ImageType
    (0x0008, 0x0012): IMAGE,   # InstanceCreationDate
    (0x0008, 0x0013): IMAGE,   # InstanceCreationTime
    (0x0008, 0x0022): IMAGE,   # AcquisitionDate
    (0x0008, 0x0023): IMAGE,   # ContentDate
    (0x0008, 0x0032): IMAGE,   # AcquisitionTime
    (0x0008, 0x0033): IMAGE,   # ContentTime
    (0x0020, 0x0012): IMAGE,   # AcquisitionNumber
    (0x0020, 0x0013): IMAGE,   # InstanceNumber
    (0x0020, 0x0032): IMAGE,   # ImagePositionPatient
    (0x0020, 0x0037): IMAGE,   # ImageOrientationPatient
}

# Per-group default when a tag isn't curated above.
_GROUP_DEFAULT = {
    0x0002: OTHER,       # File Meta Information
    0x0004: OTHER,       # Directory
    0x0010: PATIENT,
    0x0012: PATIENT,     # Clinical Trial Subject
    0x0038: STUDY,       # Visit / admission
    0x0032: STUDY,       # Study scheduling / comments
    0x0018: IMAGE,       # Acquisition parameters
    0x0028: IMAGE,       # Image Pixel / presentation
    0x7FE0: IMAGE,       # Pixel Data
    0x0040: OTHER,       # Procedure step / content (very mixed)
}

_TAG_RE = re.compile(r"\(?([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{4})\)?")


def classify(group: int, element: int) -> str:
    if group & 1:                      # odd group -> private
        return PRIVATE
    hit = _CURATED.get((group, element))
    if hit:
        return hit
    return _GROUP_DEFAULT.get(group, OTHER)


def classify_tag_str(tag_str: str) -> str:
    """Classify from a '(gggg,eeee)' string (ignores any item prefix)."""
    m = _TAG_RE.search(tag_str or "")
    if not m:
        return OTHER
    return classify(int(m.group(1), 16), int(m.group(2), 16))
