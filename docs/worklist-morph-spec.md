# Design spec — Worklist Morph (test-order builder)

*Status: draft for review. Target: DICOM Toolkit. No real AE titles, hosts, IPs or
PHI appear in this document (per project policy); all such values are placeholders.*

## 1. Purpose

Generate realistic **test studies** by taking a real study's pixel data and
re-stamping it with the identity of a **scheduled order** from a Modality Worklist
(DMWL). Lets a tester drive a worklist-ordered study into a test VNA/PACS without
hand-crafting DICOM, to exercise order → exam → archive → viewer workflows.

Requested flow (verbatim intent):

1. Query the DMWL for scheduled orders.
2. For a chosen order, search the **Production VNA** (C-FIND) for a study matching
   the order's **Study Description**.
3. **C-GET** the matched study to local storage.
4. Apply worklist attributes onto the downloaded study (Study Instance UID,
   Patient Name, DOB, Sex, Accession Number, Study Date, Ordering Physician,
   Scheduled Station AE).
5. **C-STORE** the updated study to the **Test VNA**.
6. Delete the local copy.

## 2. Goals / non-goals

**Goals**
- One guided workflow (new "Worklist Morph" tab) that composes existing engine
  primitives; no new network or transform engine code of consequence.
- Reproducible: a tester can re-run an order against a new source study.
- Safe by default: no real PHI reaches the test VNA unless explicitly allowed.

**Non-goals**
- Not a load/throughput generator (one order at a time; batch is a later option).
- Not a worklist *server* (we query a DMWL, we don't serve one).
- Not MPPS emission in v1 (optional later; the toolkit already has `net/mpps`).

## 3. Reuses what already exists

Almost everything is already in the toolkit — this feature is orchestration:

| Step | Existing component |
|---|---|
| DMWL query | `net/mwl.query_worklist`, `build_worklist_query` |
| Production VNA search | `net/scu.c_find` (STUDY level) |
| Download study | `net/scu.c_get` (writes to a local dir) |
| Re-stamp identity + fresh UIDs + zero preamble | `print2pacs/dicom_import.restamp` (via a `model.StudyContext`) |
| Optional scrub (de-id / sanitize / burned-in guard) | `store/processing.Processor` + `tools/sanitize` |
| Send to Test VNA | `net/scu.c_store` |
| Destination picker / nodes | `model.Node`, `DestinationPicker` (PACS/Storage + Worklist groups) |

`restamp()` already overwrites PatientName/ID/BirthDate/Sex, StudyInstanceUID/
Date/Time, AccessionNumber, StudyID, StudyDescription, ReferringPhysicianName,
mints a **new Series + SOP Instance UID**, and zeros the preamble — i.e. it already
covers most of the requested attribute set and the UID-collision concern (§6).

## 4. The four design decisions

### 4.1 PHI handling — the gating decision
Re-stamping the eight requested attributes is **not** de-identification. The
downloaded study is a real patient's; everything not overwritten still carries
their PHI into the test VNA:
- other identifiers (`OtherPatientIDs`, `OtherPatientIDsSequence`,
  `IssuerOfPatientID`), `InstitutionName`, acquisition/other dates, private tags;
- **burned-in pixel text** (ultrasound/SC banners, CT dose screens).

**Decision required:** is the Test VNA a PHI-approved environment?
- **Default (recommended): "Scrub" mode.** Run the study through
  `Processor` with the Basic Profile + structural **sanitize** + the **burned-in
  guard**, *then* apply the order identity on top. Real PHI never leaves.
- **"Verbatim" mode** (only if the Test VNA is explicitly PHI-approved): stamp the
  eight attributes only, leave the rest. Must be a deliberate, labeled choice with
  an on-screen warning — never the default.

Order of operations in Scrub mode: **scrub first, then stamp the order identity**
(so the Basic Profile's own UID remap/date logic doesn't fight the order values).

### 4.2 Study matching / disambiguation
Study Description alone is not unique (a real query returned ~580 matches). Needs:
- Narrow the C-FIND with additional keys where available: **Modality**, and
  optionally body part / date range / min instance count.
- A **pick strategy**: operator selects from a results list (v1 default), or an
  automatic rule ("most recent", "most instances", "random") for unattended runs.
- Explicit **no-match** and **too-many-matches** handling (don't silently pick).

### 4.3 UID handling
- Apply the order's **Study Instance UID** (0020,000D) to every object.
- **Mint fresh Series Instance UID and SOP Instance UID** per object (as
  `restamp()` does) so reusing one source study for multiple orders — or re-sending
  — can't collide at the Test VNA. Keep `FrameOfReferenceUID` internally consistent
  within the morphed study (regenerate once per study if changed).

### 4.4 Attribute mapping (resolve the two ambiguous ones)
| Order field (MWL) | DICOM attribute written | Notes |
|---|---|---|
| Study Instance UID | `(0020,000D) StudyInstanceUID` | from MWL SPS / study |
| Patient Name | `(0010,0010) PatientName` | |
| Patient DOB | `(0010,0030) PatientBirthDate` | |
| Patient Sex | `(0010,0040) PatientSex` | |
| Accession Number | `(0008,0050) AccessionNumber` | |
| Study Date | `(0008,0020) StudyDate` | also set `StudyTime`? (decide) |
| Ordering Physician | **clarify** → `(0008,0090) ReferringPhysicianName` or `(0032,1032) RequestingPhysician` | pick per site convention |
| Scheduled Station AE | **clarify** → `(0040,0001) ScheduledStationAETitle` has no home in an image object | likely for routing/MPPS, not an image tag — confirm intent |

Also set `PatientID` from the order (the request lists Patient Name/DOB/Sex but not
the MRN — confirm whether the order's Patient ID should be applied too; usually
yes for worklist correlation).

## 5. Workflow / UX (new "Worklist Morph" tab)

1. **Pick worklist source** (Worklist/MPPS destination group) → **Query DMWL** →
   list scheduled orders (counts-only display; order identity is PHI on real DMWLs,
   so render via `LogBox.set_text`, never `log.write`).
2. **Select an order.** Show its attributes.
3. **Pick Production VNA** (PACS/Storage group) → auto C-FIND by Study Description
   (+ modality) → **results list**; operator picks the source study (or auto-rule).
4. **Mode:** Scrub (default) or Verbatim (guarded, warns).
5. **Pick Test VNA** (PACS/Storage group).
6. **Run:** C-GET → (scrub) → restamp with the order's `StudyContext` → C-STORE →
   delete local temp. Progress + per-object counts (no PHI in the file log).

## 6. Safety & cleanup
- **Local temp** under the toolkit data dir; deleted on success *and* on failure
  (`try/finally`); never leave a real study on disk.
- **Preamble** zeroed on every written object (already unconditional via
  `save_dicom`/`restamp`).
- **Verbatim mode** requires an explicit toggle + visible warning naming the Test
  VNA; log the mode used (not the values).
- Honor existing TLS / allowlist settings for all three associations.

## 7. Error handling
- DMWL / C-FIND association failure → clear message, abort.
- Zero or multiple matches → operator resolves (or the auto-rule decides, logged).
- C-GET partial / failed → do not send; report which objects arrived.
- C-STORE partial → report per-object status; local temp still cleaned up.

## 8. Testing (engine-level, synthetic only)
- Build a synthetic `StudyContext` from a fake MWL item; `restamp()` applies all
  mapped attributes and mints new Series/SOP UIDs (extend existing
  `test_dicom_import_p2p` / `test_reconcile_p2p`).
- Scrub mode: a source with `OtherPatientIDs` + a non-zero preamble + an overlay →
  after scrub+stamp, PHI carriers gone, order identity present, preamble zero.
- Matching: C-FIND helper returns 0 / 1 / many → correct branch.
- Verbatim mode leaves non-mapped tags intact (and is clearly flagged).
- UID uniqueness across two morphs of the same source study.

## 9. Open questions for the requester
1. Is the Test VNA PHI-approved, or must studies be scrubbed? (Sets the default.)
2. "Ordering Physician" → ReferringPhysicianName or RequestingPhysician?
3. "Scheduled Station AE" → what should it set, and where? (No image-object home.)
4. Apply the order's **Patient ID / MRN** too?
5. Attended (operator picks the study) or unattended (auto-rule) for v1?
6. Single order per run in v1, or batch?

## 10. Rough effort
Small-to-moderate: one new panel + a thin orchestration module wiring existing
primitives, a `StudyContext`-from-MWL mapper, and the scrub toggle. No new engine
subsystems. The design risks are policy (PHI mode) and matching UX, not code.
