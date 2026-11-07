"""Core data models shared across the toolkit."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, asdict


AE_TITLE_MAX = 16


def validate_aetitle(ae: str, *, allow_empty: bool = False) -> str:
    """Check a DICOM AE Title (PS3.5 ``AE`` value representation).

    Returns '' when valid, else a short human-readable reason. Rules: 1-16
    characters, not empty / not all spaces, printable ASCII only (0x20-0x7E, no
    control or non-ASCII characters), and no backslash (the DICOM multi-value
    delimiter).
    """
    if ae is None or ae == "":
        return "" if allow_empty else "cannot be empty"
    if ae.strip() == "":
        return "cannot be all spaces"
    if len(ae) > AE_TITLE_MAX:
        return f"too long ({len(ae)} chars; max {AE_TITLE_MAX})"
    if "\\" in ae:
        return "backslash (\\) is not allowed"
    bad = [c for c in ae if ord(c) < 0x20 or ord(c) > 0x7E]
    if bad:
        return "only printable ASCII is allowed (no control/non-ASCII chars)"
    return ""

# Destination groups. Storage targets (PACS/VNA/workstations) are the default;
# worklist/MPPS servers (RIS) are a separate kind so they don't show up in the
# storage-oriented pickers (Send, Query, Retrieve, ...).
DEST_GROUP_STORAGE = "PACS / Storage"
DEST_GROUP_WORKLIST = "Worklist / MPPS"
DEST_GROUP_HL7 = "HL7 / MLLP"          # HL7 v2 over MLLP (no AE title)
DEST_GROUPS = [DEST_GROUP_STORAGE, DEST_GROUP_WORKLIST, DEST_GROUP_HL7]


@dataclass
class Node:
    """A remote DICOM peer (PACS, VNA, workstation, etc.).

    ``name`` is the friendly label shown in dropdowns; the DICOM identity is the
    (aetitle, host, port) triple.
    """

    name: str
    aetitle: str
    host: str
    port: int
    note: str = ""
    timeout: int = 30
    tls: bool = False
    # Which picker(s) this destination shows up in (storage vs worklist/MPPS).
    group: str = DEST_GROUP_STORAGE
    # Optional local (calling) AE Title to present when associating with THIS
    # node. Empty means fall back to the app-wide "My AE Title". Lets a single
    # tool talk to, e.g., a worklist server that only answers a registered
    # modality AE, without changing the global identity used everywhere else.
    calling_aetitle: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=str(d.get("name", "")).strip(),
            aetitle=str(d.get("aetitle", "")).strip(),
            host=str(d.get("host", "")).strip(),
            port=int(d.get("port", 104) or 104),
            note=str(d.get("note", "")).strip(),
            timeout=int(d.get("timeout", 30) or 30),
            tls=bool(d.get("tls", False)),
            group=str(d.get("group", "") or DEST_GROUP_STORAGE).strip(),
            calling_aetitle=str(d.get("calling_aetitle", "")).strip(),
        )

    def calling(self, default: str) -> str:
        """The local AE Title to present for this node (override, else default)."""
        return self.calling_aetitle or default

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means valid."""
        problems: list[str] = []
        if not self.name:
            problems.append("Name is empty.")
        # HL7/MLLP endpoints have no AE title; DICOM destinations require one.
        hl7 = self.group == DEST_GROUP_HL7
        ae_problem = validate_aetitle(self.aetitle, allow_empty=hl7)
        if ae_problem:
            problems.append(f"AE Title '{self.aetitle}': {ae_problem}.")
        calling_problem = validate_aetitle(self.calling_aetitle, allow_empty=True)
        if calling_problem:
            problems.append(f"Calling AE '{self.calling_aetitle}': "
                            f"{calling_problem}.")
        if not self.host:
            problems.append("Host is empty.")
        else:
            try:
                ipaddress.ip_address(self.host)
            except ValueError:
                # Not an IP literal - allow hostnames but flag obvious typos
                if ".." in self.host or self.host.endswith("."):
                    problems.append(f"Host '{self.host}' looks malformed.")
        if not (1 <= int(self.port) <= 65535):
            problems.append(f"Port {self.port} is out of range (1-65535).")
        return problems

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.aetitle}@{self.host}:{self.port})"


# ---------------------------------------------------------------------------
# Print2PACS: study reconciliation + new-series details
# ---------------------------------------------------------------------------
@dataclass
class StudyContext:
    """Patient + study identity pulled from PACS by C-FIND, used to stamp a new
    Secondary Capture / video / re-filed series onto an *existing* study."""

    patient_id: str = ""
    patient_name: str = ""
    birth_date: str = ""       # DICOM DA: YYYYMMDD
    sex: str = ""              # M / F / O
    study_uid: str = ""        # existing StudyInstanceUID (the key value)
    study_date: str = ""
    study_time: str = ""
    accession: str = ""
    study_id: str = ""
    study_description: str = ""
    referring_physician: str = ""
    modalities: str = ""       # ModalitiesInStudy (informational)

    @property
    def is_resolved(self) -> bool:
        return bool(self.study_uid)


@dataclass
class SeriesInfo:
    """Details for the NEW series that the imported items become."""

    modality: str = "OT"                 # Secondary Capture is typically OT
    series_description: str = "Print2PACS Import"
    body_part: str = ""
    series_number: int = 9901            # high number to avoid clashing
    conversion_type: str = "WSD"         # SC Equipment: WSD/SD/SI/DF/...


# Standard DICOM Body Part Examined defined terms (PS3.16 Annex L). Uppercase,
# no spaces. The GUI offers these as suggestions but still allows a custom value.
BODY_PARTS_EXAMINED = [
    "ABDOMEN", "ABDOMENPELVIS", "ADRENAL", "ANKLE", "AORTA", "ARM", "AXILLA",
    "BACK", "BLADDER", "BRAIN", "BREAST", "BRONCHUS", "BUTTOCK", "CALCANEUS",
    "CALF", "CAROTID", "CEREBELLUM", "CERVIX", "CHEEK", "CHEST", "CHESTABDOMEN",
    "CHESTABDPELVIS", "CLAVICLE", "COCCYX", "COLON", "CORNEA", "CORONARYARTERY",
    "CSPINE", "DUODENUM", "EAR", "ELBOW", "ESOPHAGUS", "EXTREMITY", "EYE",
    "EYELID", "FACE", "FEMUR", "FINGER", "FOOT", "FOREARM", "GALLBLADDER",
    "HAND", "HEAD", "HEADNECK", "HEART", "HIP", "HUMERUS", "ILEUM", "ILIUM",
    "JAW", "JEJUNUM", "KIDNEY", "KNEE", "LARYNX", "LEG", "LIVER", "LSPINE",
    "LUNG", "MAXILLA", "MEDIASTINUM", "MOUTH", "NECK", "NOSE", "ORBIT", "OVARY",
    "PANCREAS", "PAROTID", "PATELLA", "PELVIS", "PENIS", "PHARYNX", "PROSTATE",
    "RADIUS", "RADIUSULNA", "RECTUM", "RIB", "SACRUM", "SCALP", "SCAPULA",
    "SCLERA", "SCROTUM", "SHOULDER", "SKULL", "SPINE", "SPLEEN", "SSPINE",
    "STERNUM", "STOMACH", "TESTIS", "THIGH", "THUMB", "THYMUS", "THYROID",
    "TIBIA", "TIBIAFIBULA", "TMJ", "TOE", "TONGUE", "TRACHEA", "TSPINE", "ULNA",
    "URETER", "URETHRA", "UTERUS", "VAGINA", "VULVA", "WHOLEBODY", "WRIST",
    "ZYGOMA",
]


# Allowed SC Equipment ConversionType values (PS3.3 C.8.6.1).
CONVERSION_TYPES = {
    "DV": "Digitized Video",
    "DI": "Digital Interface",
    "DF": "Digitized Film",
    "WSD": "Workstation",
    "SD": "Scanned Document",
    "SI": "Scanned Image",
    "DRW": "Drawing",
    "SYN": "Synthetic Image",
}
