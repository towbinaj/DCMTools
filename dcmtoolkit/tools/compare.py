"""Compare the DICOM tags of two objects - two images, two series, or two
studies. For series/study comparison a representative instance of each side is
used (plus a small composition summary); for images the two files are compared
directly. Top-level attributes only (sequences shown as a summary line).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydicom import dcmread
from pydicom.tag import Tag

from . import tagclass
from . import validity
from .fileops import find_dicom_files, expand_sources

_PIXEL = Tag(0x7FE0, 0x0010)


@dataclass
class DiffRow:
    tag: str          # "(gggg,eeee)"
    keyword: str
    cls: str          # tagclass
    a: str
    b: str
    status: str       # same | different | only_a | only_b
    validity: str = "na"   # valid | error | warn | na (worst of A/B)


@dataclass
class SideSummary:
    label: str = ""
    files: int = 0
    studies: int = 0
    series: int = 0
    instances: int = 0
    note: str = ""


@dataclass
class Group:
    """A distinct series or study found in a loaded set."""
    key: str                 # the Series/Study InstanceUID
    label: str               # human label for the dropdown
    representative: object    # a representative Dataset
    instances: int = 0
    series: int = 0          # (studies only) distinct series in the study


def read_one(path):
    return _read(Path(path))


def _series_label(ds, count: int) -> str:
    num = str(getattr(ds, "SeriesNumber", "") or "?")
    mod = str(getattr(ds, "Modality", "") or "?")
    desc = str(getattr(ds, "SeriesDescription", "") or "")
    base = f"Series {num} · {mod}"
    if desc:
        base += f" · {desc}"
    return f"{base}  ({count} img)"


def _study_label(ds, series: int, count: int) -> str:
    date = str(getattr(ds, "StudyDate", "") or "")
    desc = str(getattr(ds, "StudyDescription", "") or "")
    acc = str(getattr(ds, "AccessionNumber", "") or "")
    parts = [p for p in (date, desc, (f"Acc {acc}" if acc else "")) if p]
    head = " · ".join(parts) or "Study"
    return f"{head}  ({series} series, {count} img)"


def enumerate_groups(paths, level: str) -> list[Group]:
    """Distinct series (level='series') or studies (level='studies') in the
    loaded files/folders, each with a representative instance and counts."""
    files = expand_sources([Path(p) for p in paths])
    uid_kw = "SeriesInstanceUID" if level == "series" else "StudyInstanceUID"
    acc: dict = {}
    for f in files:
        try:
            ds = _read(f)
        except Exception:  # noqa: BLE001
            continue
        key = str(getattr(ds, uid_kw, "") or "")
        g = acc.get(key)
        if g is None:
            acc[key] = g = {"rep": ds, "count": 0, "series": set()}
        g["count"] += 1
        g["series"].add(str(getattr(ds, "SeriesInstanceUID", "") or ""))
    out: list[Group] = []
    for key, g in acc.items():
        if level == "series":
            label = _series_label(g["rep"], g["count"])
        else:
            label = _study_label(g["rep"], len(g["series"]), g["count"])
        out.append(Group(key=key, label=label, representative=g["rep"],
                         instances=g["count"], series=len(g["series"])))
    out.sort(key=lambda gr: gr.label.lower())
    return out


def _clip(v: str, n: int) -> str:
    v = str(v).replace("\r", " ").replace("\n", " ")
    return v if len(v) <= n else v[:n] + "..."


def _elem_value(elem) -> str:
    if elem.VR == "SQ":
        try:
            return f"<sequence, {len(elem.value)} item(s)>"
        except Exception:  # noqa: BLE001
            return "<sequence>"
    return str(elem.value)


def dataset_tags(ds) -> dict:
    out = {}
    for elem in ds:
        if elem.tag == _PIXEL:
            continue
        out[(elem.tag.group, elem.tag.element)] = (
            elem.keyword or "", _elem_value(elem), elem.VR or "")
    return out


def diff_datasets(a, b, *, max_len: int = 200,
                  conf_a: dict | None = None,
                  conf_b: dict | None = None) -> list[DiffRow]:
    """Tag-by-tag diff of two datasets, sorted by tag. ``conf_a``/``conf_b`` are
    optional per-tag IOD conformance code maps (int tag -> code) that sharpen the
    per-row validity bucket."""
    conf_a, conf_b = conf_a or {}, conf_b or {}
    ta, tb = dataset_tags(a), dataset_tags(b)
    rows: list[DiffRow] = []
    for key in sorted(set(ta) | set(tb)):
        g, e = key
        tag_int = (g << 16) | e
        ka, kb = ta.get(key), tb.get(key)
        keyword = (ka or kb)[0]
        av = ka[1] if ka else ""
        bv = kb[1] if kb else ""
        if ka and kb:
            status = "same" if av == bv else "different"
        elif ka:
            status = "only_a"
        else:
            status = "only_b"
        sa = (validity.status(ka[2], av, conf_a.get(tag_int)) if ka else "na")
        sb = (validity.status(kb[2], bv, conf_b.get(tag_int)) if kb else "na")
        rows.append(DiffRow(f"({g:04X},{e:04X})", keyword,
                            tagclass.classify(g, e),
                            _clip(av, max_len), _clip(bv, max_len), status,
                            validity.worst(sa, sb)))
    return rows


def _read(path: Path):
    return dcmread(str(path), force=True, stop_before_pixels=True)


def load_side(paths, *, mode: str, label: str) -> tuple[object, SideSummary]:
    """Resolve one side to (representative_dataset, summary).

    ``mode`` is 'images' (expects one file), 'series' or 'studies' (a folder /
    set; a representative instance is chosen and composition is summarised).
    Raises ValueError if nothing readable is found.
    """
    files = [Path(p) for p in paths]
    if mode == "images":
        dicom = [f for f in files if f.is_file()]
    else:
        dicom = expand_sources(files)
    studies, series = set(), set()
    representative = None
    instances = 0
    for f in dicom:
        try:
            ds = _read(f)
        except Exception:  # noqa: BLE001
            continue
        instances += 1
        studies.add(str(getattr(ds, "StudyInstanceUID", "")))
        series.add(str(getattr(ds, "SeriesInstanceUID", "")))
        if representative is None:
            representative = ds
    if representative is None:
        raise ValueError("No readable DICOM object found.")
    summ = SideSummary(label=label, files=len(dicom),
                       studies=len([s for s in studies if s]),
                       series=len([s for s in series if s]),
                       instances=instances)
    if mode == "series" and summ.series > 1:
        summ.note = f"folder has {summ.series} series - comparing a representative"
    if mode == "studies" and summ.studies > 1:
        summ.note = f"folder has {summ.studies} studies - comparing a representative"
    return representative, summ
