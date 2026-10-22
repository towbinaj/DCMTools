"""Tag classification used by the Tag Lister class filter."""

from __future__ import annotations

from dcmtoolkit.tools import tagclass as tc


def test_classify_known_tags():
    assert tc.classify(0x0010, 0x0010) == tc.PATIENT      # PatientName
    assert tc.classify(0x0020, 0x000D) == tc.STUDY        # StudyInstanceUID
    assert tc.classify(0x0008, 0x0050) == tc.STUDY        # AccessionNumber
    assert tc.classify(0x0020, 0x000E) == tc.SERIES       # SeriesInstanceUID
    assert tc.classify(0x0008, 0x0060) == tc.SERIES       # Modality
    assert tc.classify(0x0008, 0x0080) == tc.EQUIPMENT    # InstitutionName
    assert tc.classify(0x0020, 0x0052) == tc.FRAME        # FrameOfReferenceUID
    assert tc.classify(0x0008, 0x0018) == tc.IMAGE        # SOPInstanceUID
    assert tc.classify(0x0028, 0x0010) == tc.IMAGE        # Rows (group default)
    assert tc.classify(0x7FE0, 0x0010) == tc.IMAGE        # PixelData
    assert tc.classify(0x0002, 0x0010) == tc.OTHER        # TransferSyntax (meta)


def test_private_group_is_private():
    assert tc.classify(0x0009, 0x0010) == tc.PRIVATE
    assert tc.classify(0x0029, 0x1010) == tc.PRIVATE


def test_classify_from_string_ignores_item_prefix():
    assert tc.classify_tag_str("(0010,0010)") == tc.PATIENT
    assert tc.classify_tag_str("  item0 (0008,0060)") == tc.SERIES
    assert tc.classify_tag_str("garbage") == tc.OTHER
