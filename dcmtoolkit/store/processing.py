"""De-identification / morphing / pixel-blanking for received objects.

Implements the legacy DCMStoreService [ANONYMIZE] and [MORPH] behavior:

* RemovePrivateTags, RemoveGroups, RemoveTags
* AnonymizeTags via an anonymize file (REMOVE / BLANK / replacement value),
  with consistent Study/Series/SOP UID remapping
* CalculatedDates (baseline 19000101, age-preserving)
* RemoveImageTop / RemoveImageLeft pixel blanking, with modality filters
* MorphTags: look up a search-tag value in a morph file and replace tags
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from pydicom.dataset import Dataset
from pydicom.tag import Tag
from pydicom.uid import generate_uid

from ..logging_setup import get_logger

log = get_logger("store.processing")

BASELINE = date(1900, 1, 1)
_DATE_TAGS = ["StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate"]


@dataclass
class ReceiverConfig:
    # [DICOM]
    aetitle: str = "STORESCP"
    port: int = 104
    save_folder: str = ""
    folder_format: str = "PATIENT"
    # TLS (secure receiver)
    tls: bool = False
    tls_cert_file: str = ""
    tls_key_file: str = ""
    tls_ca_file: str = ""
    require_client_cert: bool = False
    # [ANONYMIZE]
    remove_private_tags: bool = False
    remove_groups: list[str] = field(default_factory=list)   # ["6000", ...]
    remove_tags: list[str] = field(default_factory=list)     # ["00081040", ...]
    anonymize_tags: bool = False
    anonymize_file: str = ""
    calculated_dates: bool = False
    remove_image_top: int = 0
    remove_image_top_modality: list[str] = field(default_factory=list)
    remove_image_left: int = 0
    remove_image_left_modality: list[str] = field(default_factory=list)
    # [MORPH]
    morph_tags: bool = False
    morphing_file_format: str = ""   # "00080050|00100010|00100020"
    morphing_file: str = ""


def _parse_tag(s: str) -> Tag:
    s = s.strip()
    return Tag(int(s[:4], 16), int(s[4:], 16))


def _parse_date(v: str) -> date | None:
    v = (v or "").strip()
    if len(v) >= 8 and v[:8].isdigit():
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return None


class Processor:
    """Stateful processor; keeps UID and morph tables across objects."""

    def __init__(self, cfg: ReceiverConfig):
        self.cfg = cfg
        self._uid_map: dict[str, str] = {}
        self._uid_lock = threading.Lock()
        self._anon_rules = self._load_anon_rules()
        self._morph_format = self._load_morph_format()
        self._morph_table = self._load_morph_table()

    # -- loading ---------------------------------------------------------
    def _load_anon_rules(self) -> list[tuple[Tag, str]]:
        rules = []
        if self.cfg.anonymize_tags and self.cfg.anonymize_file:
            p = Path(self.cfg.anonymize_file)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("|")
                    if len(parts) >= 2:
                        try:
                            rules.append((_parse_tag(parts[0]), parts[1]))
                        except ValueError:
                            log.warning("Bad anonymize line: %s", line)
        return rules

    def _load_morph_format(self) -> list[Tag]:
        if not (self.cfg.morph_tags and self.cfg.morphing_file_format):
            return []
        tags = []
        for part in self.cfg.morphing_file_format.split("|"):
            try:
                tags.append(_parse_tag(part))
            except ValueError:
                log.error("Malformed MorphingFileFormat token: %s", part)
                return []
        return tags

    def _load_morph_table(self) -> dict[str, list[str]]:
        table: dict[str, list[str]] = {}
        if self.cfg.morph_tags and self.cfg.morphing_file:
            p = Path(self.cfg.morphing_file)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.rstrip("\n")
                    if not line or line.startswith("#"):
                        continue
                    vals = line.split("|")
                    key = vals[0].strip()
                    if key and key not in table:
                        table[key] = vals
        return table

    # -- per-object processing ------------------------------------------
    def process(self, ds: Dataset) -> Dataset:
        if self.cfg.morph_tags:
            self._apply_morph(ds)
        if self.cfg.remove_private_tags:
            ds.remove_private_tags()
        self._apply_remove_groups(ds)
        self._apply_remove_tags(ds)
        if self.cfg.anonymize_tags:
            if self.cfg.calculated_dates:
                self._apply_calculated_dates(ds)
            self._apply_anon_rules(ds)
            self._remap_uids(ds)
        self._apply_pixel_blanking(ds)
        return ds

    def _apply_remove_groups(self, ds: Dataset) -> None:
        for grp in self.cfg.remove_groups:
            try:
                g = int(grp, 16)
            except ValueError:
                continue
            for elem in list(ds):
                if elem.tag.group == g:
                    del ds[elem.tag]

    def _apply_remove_tags(self, ds: Dataset) -> None:
        for t in self.cfg.remove_tags:
            try:
                tag = _parse_tag(t)
            except ValueError:
                continue
            if tag in ds:
                del ds[tag]

    def _apply_anon_rules(self, ds: Dataset) -> None:
        for tag, option in self._anon_rules:
            if tag not in ds:
                continue
            if option.upper() == "REMOVE":
                del ds[tag]
            elif option.upper() == "BLANK":
                ds[tag].value = ""
            else:
                ds[tag].value = option

    def _apply_calculated_dates(self, ds: Dataset) -> None:
        birth = _parse_date(getattr(ds, "PatientBirthDate", ""))
        study = _parse_date(getattr(ds, "StudyDate", ""))
        if birth and study:
            age_days = (study - birth).days
            new_study = BASELINE + timedelta(days=max(age_days, 0))
        else:
            new_study = BASELINE
        offset = None
        if study:
            offset = (new_study - study).days
        for attr in _DATE_TAGS:
            cur = _parse_date(getattr(ds, attr, ""))
            if cur and offset is not None:
                nd = cur + timedelta(days=offset)
                setattr(ds, attr, nd.strftime("%Y%m%d"))
            elif attr == "StudyDate":
                setattr(ds, attr, new_study.strftime("%Y%m%d"))
        if "PatientBirthDate" in ds:
            ds.PatientBirthDate = BASELINE.strftime("%Y%m%d")

    def _remap(self, uid: str) -> str:
        # Locked: parallel de-identify workers share one Processor so that the
        # same source UID always maps to the same new UID within a study.
        with self._uid_lock:
            if uid not in self._uid_map:
                self._uid_map[uid] = generate_uid()
            return self._uid_map[uid]

    def _remap_uids(self, ds: Dataset) -> None:
        for attr in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
            if getattr(ds, attr, None):
                setattr(ds, attr, self._remap(str(getattr(ds, attr))))
        if getattr(ds, "file_meta", None) is not None:
            if getattr(ds.file_meta, "MediaStorageSOPInstanceUID", None):
                ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    def _apply_morph(self, ds: Dataset) -> None:
        if not self._morph_format or not self._morph_table:
            return
        search_tag = self._morph_format[0]
        if search_tag not in ds:
            return
        key = str(ds[search_tag].value).strip()
        row = self._morph_table.get(key)
        if not row:
            return
        # format[i] (i>=1) receives row[i]
        for i, tag in enumerate(self._morph_format):
            if i == 0:
                continue
            if i < len(row):
                if tag in ds:
                    ds[tag].value = row[i]
                else:
                    ds.add_new(tag, "LO", row[i])

    def _apply_pixel_blanking(self, ds: Dataset) -> None:
        top = self.cfg.remove_image_top
        left = self.cfg.remove_image_left
        if top <= 0 and left <= 0:
            return
        modality = str(getattr(ds, "Modality", "")).upper()
        do_top = top > 0 and (not self.cfg.remove_image_top_modality
                              or modality in
                              [m.upper() for m in
                               self.cfg.remove_image_top_modality])
        do_left = left > 0 and (not self.cfg.remove_image_left_modality
                                or modality in
                                [m.upper() for m in
                                 self.cfg.remove_image_left_modality])
        if not (do_top or do_left):
            return
        try:
            ts = ds.file_meta.TransferSyntaxUID
            if ts.is_compressed:
                log.warning("Cannot blank pixels of compressed image %s",
                            getattr(ds, "SOPInstanceUID", "?"))
                return
            arr = ds.pixel_array
        except Exception as exc:  # noqa: BLE001
            log.warning("Pixel blanking skipped: %s", exc)
            return

        rows = arr.shape[-2] if arr.ndim >= 2 else 0
        cols = arr.shape[-1] if arr.ndim >= 2 else 0
        # handle multi-sample last axis
        if arr.ndim == 3 and arr.shape[-1] in (3, 4):
            rows, cols = arr.shape[0], arr.shape[1]

        if do_top and rows:
            n = max(1, int(rows * top / 100))
            arr[..., :n, :] = 0
        if do_left and cols:
            n = max(1, int(cols * left / 100))
            arr[..., :, :n] = 0
        ds.PixelData = arr.tobytes()
