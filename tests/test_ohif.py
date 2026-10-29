"""OHIF viewer bridge: dicomjson manifest + the local 127.0.0.1 server."""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
from pydicom.uid import generate_uid

from dcmtoolkit.tools import ohif
from conftest import make_ct


def _write(tmp_path, name, **over):
    ds = make_ct(**over)
    p = tmp_path / name
    ds.save_as(p, enforce_file_format=True)
    return p, ds


def _url(token):
    return f"http://h/f/{token}"


def test_manifest_groups_sorts_and_naturalizes(tmp_path):
    st, se = generate_uid(), generate_uid()
    p2, _ = _write(tmp_path, "b.dcm", StudyInstanceUID=st, SeriesInstanceUID=se,
                   extra={"InstanceNumber": 2, "PixelSpacing": [0.5, 0.5],
                          "WindowCenter": "40"})
    p1, ds1 = _write(tmp_path, "a.dcm", StudyInstanceUID=st,
                     SeriesInstanceUID=se, extra={"InstanceNumber": 1})
    (tmp_path / "notes.txt").write_text("not dicom")
    m = ohif.build_manifest(sorted(tmp_path.iterdir()), _url)

    assert m.skipped == 1 and m.instance_count == 2
    (study,) = m.data["studies"]
    assert study["StudyInstanceUID"] == st
    assert study["PatientName"] == "DOE^JANE" and study["NumInstances"] == 2
    (series,) = study["series"]
    first, second = series["instances"]
    assert first["metadata"]["SOPInstanceUID"] == ds1.SOPInstanceUID
    meta = second["metadata"]
    assert meta["PixelSpacing"] == [0.5, 0.5]          # multi-value -> list
    assert meta["WindowCenter"] == 40.0                # DS -> number
    assert meta["Rows"] == 4                           # US -> int
    assert "PixelData" not in meta                     # bulk data never sent
    assert first["url"].startswith("dicomweb:http://h/f/")
    json.dumps(m.data)                                 # serialisable


def test_multiframe_gets_one_entry_per_frame(tmp_path):
    p, ds = _write(tmp_path, "mf.dcm", extra={"NumberOfFrames": 12})
    m = ohif.build_manifest([p], _url)
    insts = m.data["studies"][0]["series"][0]["instances"]
    # Numeric frame order (frame 10 must not sort before frame 2).
    assert [i["url"].rsplit("=", 1)[-1] for i in insts] == [
        str(n) for n in range(1, 13)]
    assert m.data["studies"][0]["NumInstances"] == 1
    # Only frame 1 carries the header; the rest are UID stubs.
    assert insts[0]["metadata"]["Rows"] == 4
    assert all(i["metadata"] == {"SOPInstanceUID": ds.SOPInstanceUID}
               for i in insts[1:])


def test_manifest_size_is_linear_in_frames(tmp_path):
    """Per-frame functional groups grow with frames; the header must not be
    repeated per frame (that was quadratic and exhausted memory)."""
    from pydicom.dataset import Dataset

    def size(n):
        groups = []
        for i in range(n):
            pos = Dataset()
            pos.ImagePositionPatient = [0, 0, float(i)]
            g = Dataset()
            g.PlanePositionSequence = [pos]
            groups.append(g)
        p, _ = _write(tmp_path, f"e{n}.dcm", extra={
            "NumberOfFrames": n, "PerFrameFunctionalGroupsSequence": groups})
        return len(json.dumps(ohif.build_manifest([p], _url).data))

    assert size(800) < 5 * size(200)


def test_private_and_sequences(tmp_path):
    from pydicom.dataset import Dataset
    item = Dataset()
    item.CodeValue = "123"
    p, ds = _write(tmp_path, "x.dcm", extra={"ReferencedImageSequence": [item]})
    ds = ohif.dcmread(p)
    ds.add_new(0x00091001, "LO", "secret-vendor-thing")
    ds.save_as(p)
    meta = ohif.build_manifest([p], _url).data["studies"][0]["series"][0][
        "instances"][0]["metadata"]
    assert meta["ReferencedImageSequence"] == [{"CodeValue": "123"}]
    assert "secret-vendor-thing" not in json.dumps(meta)


@pytest.fixture
def served(tmp_path, monkeypatch):
    web = tmp_path / "ohif"
    web.mkdir()
    (web / "index.html").write_text("<html>OHIF</html>")
    (web / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(ohif, "ohif_dir", lambda: web)
    data = tmp_path / "data"
    data.mkdir()
    f, _ = _write(data, "a.dcm")
    url, m = ohif.prepare([f])
    yield url, m, f
    ohif.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return r.status, r.headers, body


def test_server_serves_manifest_files_and_spa(served):
    url, m, f = served
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    assert parts.hostname == "127.0.0.1"
    q = parse_qs(parts.query)
    assert q["StudyInstanceUIDs"] == m.study_uids

    _, _, body = _get(q["url"][0])
    manifest = json.loads(body)
    file_url = manifest["studies"][0]["series"][0]["instances"][0]["url"]
    status, headers, body = _get(file_url.removeprefix("dicomweb:"))
    assert body == f.read_bytes()
    assert headers["Content-Type"] == "application/dicom"
    assert headers["Cross-Origin-Embedder-Policy"] == "require-corp"

    assert b"window.config" in _get(base + "/app-config.js")[2]
    assert _get(base + "/app.js")[2] == b"console.log(1)"
    # Client-side route -> index.html
    assert _get(url)[2] == b"<html>OHIF</html>"


def test_server_only_serves_registered_files(served, tmp_path):
    url, _, _ = served
    base = url.split("/viewer")[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/dcmtk/file/not-a-token")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        _get(base + "/dcmtk/manifest/nope.json")
    # Traversal falls back to the SPA page, never a file outside the build.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    assert b"TOPSECRET" not in _get(base + "/../secret.txt")[2]
    assert b"TOPSECRET" not in _get(base + "/%2e%2e/secret.txt")[2]


def test_prepare_rejects_no_images(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_text("nope")
    try:
        with pytest.raises(ValueError):
            ohif.prepare([tmp_path / "x.txt"])
    finally:
        ohif.shutdown()


def test_old_opens_are_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(ohif, "MAX_OPENS", 2)
    f, _ = _write(tmp_path, "a.dcm")
    try:
        urls = [ohif.prepare([f])[0] for _ in range(3)]
        srv = ohif.server()
        assert len(srv.manifests) == 2 and len(srv.files) == 2
        first = parse_qs(urlsplit(urls[0]).query)["url"][0]
        with pytest.raises(urllib.error.HTTPError):
            _get(first)
        _get(parse_qs(urlsplit(urls[-1]).query)["url"][0])
    finally:
        ohif.shutdown()


@pytest.mark.parametrize("raw, expected", [
    ("LINEAR", ["LINEAR"]),          # loader reads [0]: must be a list
    (["LINEAR", "LINEAR"], ["LINEAR"]),
    ("sigmoid", ["SIGMOID"]),
    ("LINEAR_EXACT", ["LINEAR_EXACT"]),
    ("VENDOR_CURVE", None),
])
def test_voi_lut_function_is_sanitised(tmp_path, raw, expected):
    p, _ = _write(tmp_path, "v.dcm", extra={"VOILUTFunction": raw})
    meta = ohif.build_manifest([p], _url).data["studies"][0]["series"][0][
        "instances"][0]["metadata"]
    assert meta.get("VOILUTFunction") == expected


def test_cancelled_download_is_not_reported(capsys):
    srv = ohif.server()
    try:
        try:
            raise ConnectionResetError(10054, "reset by peer")
        except ConnectionResetError:
            srv.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
    finally:
        ohif.shutdown()
