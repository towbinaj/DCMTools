"""Import destinations from the legacy DCM* CSV configuration files.

Handles both legacy layouts:

* **DCMSend.csv** - bare rows of ``name,aetitle,host,port``.
* **DCMQueryMove.csv / DCMDropMove.csv** - a ``MyAETitle,...`` line, then
  ``MoveSource,name,aetitle,host,port`` rows and ``MoveDest,...`` rows.

Because the same friendly name (e.g. "PACS Prod") points at different hosts in
different legacy files, the importer keeps every distinct entry and returns a
list of conflict warnings so a human can reconcile them.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .model import Node, DEST_GROUPS, DEST_GROUP_STORAGE


LEGACY_FILES = ["DCMSend.csv", "DCMQueryMove.csv", "DCMDropMove.csv"]


@dataclass
class ImportResult:
    nodes: list[Node]
    conflicts: list[str]
    warnings: list[str]
    source_files: list[str]


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if row:
                yield [c.strip() for c in row]


def _node_from_cols(cols: list[str], source: str) -> Node | None:
    """Build a Node from a 4/5-column (name, aetitle, host, port[, note]) row."""
    if len(cols) < 4:
        return None
    name, aetitle, host, port = cols[0], cols[1], cols[2], cols[3]
    if not aetitle or not host:
        return None
    try:
        port_i = int(port)
    except ValueError:
        return None
    if port_i <= 0:
        return None
    calling = cols[4].strip() if len(cols) >= 5 else ""
    group = cols[5].strip() if len(cols) >= 6 else ""
    if group not in DEST_GROUPS:
        group = DEST_GROUP_STORAGE
    return Node(name=name or aetitle, aetitle=aetitle, host=host,
               port=port_i, note=f"imported from {source}",
               calling_aetitle=calling, group=group)


def parse_file(path: Path) -> list[Node]:
    nodes: list[Node] = []
    for cols in _rows(path):
        tag = cols[0].lower()
        if tag == "myaetitle":
            continue
        if tag == "movedest":
            continue  # a target AE reference, not a full node definition
        if tag == "movesource":
            node = _node_from_cols(cols[1:], path.name)
        else:
            node = _node_from_cols(cols, path.name)
        if node:
            nodes.append(node)
    return nodes


def import_legacy(source_dir: Path,
                  files: list[str] | None = None) -> ImportResult:
    files = files or LEGACY_FILES
    found: list[str] = []
    all_nodes: list[Node] = []

    for fname in files:
        p = source_dir / fname
        if p.exists():
            found.append(fname)
            all_nodes.extend(parse_file(p))

    return _finalize(all_nodes, found)


def _finalize(all_nodes: list[Node], found: list[str]) -> ImportResult:
    """Deduplicate, detect conflicts, disambiguate names, and validate."""
    # Deduplicate on the full DICOM identity (aetitle, host, port).
    unique: dict[tuple[str, str, int], Node] = {}
    for n in all_nodes:
        key = (n.aetitle.upper(), n.host, n.port)
        if key not in unique:
            unique[key] = n

    nodes = list(unique.values())

    # Detect conflicts: same friendly name pointing at different hosts.
    by_name: dict[str, set[str]] = {}
    for n in nodes:
        by_name.setdefault(n.name.lower(), set()).add(f"{n.host}:{n.port}")
    conflicts: list[str] = []
    for name, endpoints in by_name.items():
        if len(endpoints) > 1:
            display = next(n.name for n in nodes if n.name.lower() == name)
            conflicts.append(
                f"'{display}' resolves to multiple endpoints: "
                + ", ".join(sorted(endpoints))
            )

    # Disambiguate duplicate names so dropdown labels stay unique: any name
    # used by more than one node gets its host appended.
    name_counts: dict[str, int] = {}
    for n in nodes:
        name_counts[n.name.lower()] = name_counts.get(n.name.lower(), 0) + 1
    for n in nodes:
        if name_counts[n.name.lower()] > 1:
            n.name = f"{n.name} ({n.host})"

    # Per-node validation warnings (e.g. a malformed host like "10.0.0..5").
    warnings: list[str] = []
    for n in nodes:
        for problem in n.validate():
            warnings.append(f"{n.name}: {problem}")

    nodes.sort(key=lambda x: x.name.lower())
    return ImportResult(nodes=nodes, conflicts=conflicts,
                        warnings=warnings, source_files=found)


def import_csv_file(path: Path) -> ImportResult:
    """Import destinations from a single CSV file.

    Accepts either the legacy layout (``MoveSource,...`` rows) or a simple
    ``name,aetitle,host,port`` layout with an optional header row.
    """
    path = Path(path)
    nodes = parse_file(path)
    return _finalize(nodes, [path.name])


def import_json_file(path: Path) -> ImportResult:
    """Import destinations from a destinations.json file."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    nodes = [Node.from_dict(d) for d in raw]
    return _finalize(nodes, [path.name])


def import_any(path: Path) -> ImportResult:
    """Import from a .json or .csv file based on extension."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return import_json_file(path)
    return import_csv_file(path)


def export_csv(nodes: list[Node], path: Path) -> None:
    """Write destinations to a simple, re-importable CSV (with header)."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "aetitle", "host", "port", "calling_aetitle",
                         "group"])
        for n in nodes:
            writer.writerow([n.name, n.aetitle, n.host, n.port,
                             n.calling_aetitle, n.group])


def export_json(nodes: list[Node], path: Path) -> None:
    Path(path).write_text(
        json.dumps([n.to_dict() for n in nodes], indent=2), encoding="utf-8")
