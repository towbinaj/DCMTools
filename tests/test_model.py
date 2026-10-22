"""Node model + destinations import/export."""

from __future__ import annotations

from dcmtoolkit.model import (Node, DEST_GROUP_STORAGE, DEST_GROUP_WORKLIST,
                              AE_TITLE_MAX, validate_aetitle)
from dcmtoolkit import importer


def test_validate_aetitle():
    assert validate_aetitle("PACS1") == ""
    assert validate_aetitle("A" * 16) == ""
    assert "empty" in validate_aetitle("")
    assert validate_aetitle("", allow_empty=True) == ""
    assert "spaces" in validate_aetitle("   ")
    assert "too long" in validate_aetitle("A" * 17)
    assert "backslash" in validate_aetitle(r"AE\X")
    assert "ASCII" in validate_aetitle("café")       # non-ASCII
    assert "ASCII" in validate_aetitle("AE\tX")       # control char


def test_node_defaults_and_roundtrip():
    n = Node(name="x", aetitle="AE", host="10.0.0.1", port=104,
             group=DEST_GROUP_WORKLIST, calling_aetitle="CALL")
    back = Node.from_dict(n.to_dict())
    assert back.group == DEST_GROUP_WORKLIST
    assert back.calling_aetitle == "CALL"
    # default group when absent
    assert Node.from_dict({"aetitle": "A", "host": "h", "port": 1}).group \
        == DEST_GROUP_STORAGE


def test_node_calling_override():
    n = Node(name="x", aetitle="AE", host="h", port=1, calling_aetitle="C")
    assert n.calling("GLOBAL") == "C"
    assert Node(name="x", aetitle="AE", host="h", port=1).calling("GLOBAL") \
        == "GLOBAL"


def test_node_validation():
    bad = Node(name="", aetitle="X" * (AE_TITLE_MAX + 1), host="", port=70000)
    problems = bad.validate()
    assert any("AE Title" in p for p in problems)
    assert any("Host" in p for p in problems)
    assert any("Port" in p for p in problems)


def test_hl7_node_allows_empty_ae():
    from dcmtoolkit.model import DEST_GROUP_HL7
    # A storage node needs an AE title...
    assert any("AE Title" in p for p in
               Node(name="s", aetitle="", host="h", port=2575).validate())
    # ...but an HL7/MLLP endpoint does not.
    hl7 = Node(name="ris", aetitle="", host="h", port=2575,
               group=DEST_GROUP_HL7)
    assert not any("AE Title" in p for p in hl7.validate())


def test_csv_export_import_roundtrip(tmp_path):
    nodes = [Node(name="A", aetitle="AE_A", host="10.0.0.1", port=104,
                  calling_aetitle="CALL", group=DEST_GROUP_WORKLIST)]
    csv_path = tmp_path / "dest.csv"
    importer.export_csv(nodes, csv_path)
    res = importer.import_csv_file(csv_path)
    assert len(res.nodes) == 1
    got = res.nodes[0]
    assert got.aetitle == "AE_A"
    assert got.calling_aetitle == "CALL"
    assert got.group == DEST_GROUP_WORKLIST


def test_json_export_import_roundtrip(tmp_path):
    nodes = [Node(name="B", aetitle="AE_B", host="10.0.0.2", port=4242)]
    jpath = tmp_path / "dest.json"
    importer.export_json(nodes, jpath)
    res = importer.import_json_file(jpath)
    assert res.nodes[0].aetitle == "AE_B" and res.nodes[0].port == 4242
