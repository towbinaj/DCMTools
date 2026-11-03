"""Tag resolution + the Modify tool's tag-picker label helpers.

All synthetic - no patient data.
"""

from __future__ import annotations

from pydicom.tag import Tag

from dcmtoolkit.tools import dicomtags

from conftest import make_ct


def test_resolve_tag_accepts_code_name_and_label():
    # SpecificCharacterSet (0008,0005) - the user's example tag.
    for text in ("00080005", "0008,0005", "0008, 0005", "SpecificCharacterSet"):
        t, kw = dicomtags.resolve_tag(text)
        assert t == Tag(0x0008, 0x0005), text
        assert kw == "SpecificCharacterSet"
    # A label with a "(gggg,eeee)" suffix (what the dropdown stores).
    t, kw = dicomtags.resolve_tag("Specific Character Set  (0008,0005)")
    assert t == Tag(0x0008, 0x0005)
    assert kw == "SpecificCharacterSet"
    # Garbage resolves to nothing.
    assert dicomtags.resolve_tag("not-a-tag") == (None, "")
    assert dicomtags.resolve_tag("") == (None, "")


def test_resolve_tag_name_is_case_and_space_insensitive():
    # Typed names resolve regardless of case or interior spacing; the returned
    # keyword is always the canonical DICOM spelling.
    for text in ("PatientName", "patientname", "PATIENTNAME",
                 "Patient Name", "patient name", "  patient   name  "):
        t, kw = dicomtags.resolve_tag(text)
        assert t == Tag(0x0010, 0x0010), text
        assert kw == "PatientName", text
    # Hex was already case-insensitive.
    assert dicomtags.resolve_tag("7fe0,0010")[0] == Tag(0x7FE0, 0x0010)


def test_dataset_tag_labels_lists_study_tags_and_round_trips():
    ds = make_ct(extra={"SpecificCharacterSet": "ISO_IR 100"})
    labels = dicomtags.dataset_tag_labels(ds)

    # Character set and other present tags are offered...
    assert any("(0008,0005)" in lab for lab in labels), labels
    assert any("PatientName" in lab and "(0010,0010)" in lab for lab in labels)

    # ...Pixel Data and group-length tags are not.
    assert not any("(7FE0,0010)" in lab for lab in labels)
    assert not any(lab.strip().endswith(",0000)") for lab in labels)

    # Every offered label resolves back to a real tag that is in the dataset.
    for lab in labels:
        t, _kw = dicomtags.resolve_tag(lab)
        assert t is not None, lab
        assert t in ds, lab


def test_dataset_tag_labels_names_unknown_tag_private():
    ds = make_ct()
    ds.add_new(Tag(0x0041, 0x1001), "LO", "x")  # private-ish / no dictionary kw
    labels = dicomtags.dataset_tag_labels(ds)
    hit = [lab for lab in labels if "(0041,1001)" in lab]
    assert hit and not hit[0].startswith("["), labels


def test_file_tag_labels_reads_header(tmp_path):
    from pydicom.uid import ExplicitVRLittleEndian
    ds = make_ct()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    p = tmp_path / "a.dcm"
    ds.save_as(str(p), enforce_file_format=True)

    labels = dicomtags.file_tag_labels(p)
    assert any("(0010,0010)" in lab for lab in labels)

    # A missing / unreadable file yields [] rather than raising (the picker
    # just falls back to the common-tag list).
    assert dicomtags.file_tag_labels(tmp_path / "does-not-exist.dcm") == []
