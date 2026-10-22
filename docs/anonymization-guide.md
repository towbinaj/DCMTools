# De-identification & anonymization — how-to

A practical guide to the **De-identify Files** tool: building custom profiles,
what each option does, date shifting, morphing, and the de-id → send pipeline.

---

## 1. The quick, safe path

1. Open **De-identify Files**, add your files/folder (or a `.zip`).
2. Leave **DICOM PS3.15 Basic Confidentiality Profile** ticked (it's on by
   default) and set an **Output folder…**.
3. Click **De-identify**.

That alone removes names, IDs, accession, institution, dates, and private tags,
replaces UIDs consistently, and stamps the de-identification markers. Everything
below is for when you need more control.

---

## 2. What the Basic Profile does (and what it leaves)

The PS3.15 **Basic Confidentiality Profile** acts on the standard list of
*confidentiality attributes* — it empties the ones that must stay present
(Type 2, e.g. `PatientID`, `AccessionNumber`), removes the ones that shouldn't
(`PatientName`'s siblings, institution identifiers), removes **all private
tags**, and re-maps UIDs consistently across the batch.

**Important:** it only touches attributes **in that standard list**. A benign
technical tag not in the list is **kept** as-is. So if your objects carry an
unusual identifying tag that isn't a standard confidentiality attribute, the
Basic Profile won't remove it — you add a rule for it (below). It also does
**not** remove burned-in pixel text — use **Remove Image Top/Left %** or the
**Preview** / **QA → Render** checks for that.

---

## 3. Custom profiles (per-tag rules)

Click **Manage profiles…** to create named profiles. A profile bundles the
Basic-Profile / retain-dates / remove-private toggles with a table of **per-tag
rules**. Each rule is one tag + an action:

| Action | What it does | Use it for |
|--------|--------------|-----------|
| **Remove** | Delete the tag entirely | An identifying tag the Basic Profile doesn't cover |
| **Blank**  | Keep the tag, empty its value | A Type-2 tag a receiver expects present but empty |
| **Replace**| Set the tag to a **fixed** value (same for every file) | Stamp a study/cohort code into `PatientID`, set `PatientName` to `ANON` |
| **Keep**   | **Exempt** the tag from the Basic Profile (do *not* anonymize it) | Keep `StudyDescription`, `BodyPartExamined`, etc. |

### How to pick tags
Type the 8-hex tag `ggggeeee` (e.g. `00100010`) — the keyword appears next to it
— or choose from the **Common tags** menu. Replace takes a value; the others
don't.

### What happens to a tag you *don't* list?
- **Basic Profile ON** (recommended): every standard confidentiality attribute
  is already handled by the profile, so you only add rules for extras
  (**Remove/Blank/Replace**) or exemptions (**Keep**). Anything not in the
  profile's list and not in your rules is **passed through unchanged**.
- **Basic Profile OFF**: *only* your rules (plus Remove-private / Remove-tags /
  Remove-groups) run — so any identifier you don't list **survives**. This is
  why leaving the Basic Profile on is strongly recommended.

Profiles are saved in `anon_profiles.json` in your local (git-ignored) data
folder — they never leave your machine.

---

## 4. Date shifting (preserve age, hide real dates)

Tick **Shift dates (DOB → 1900-01-01, keep age…)**. For each object it:

- sets **PatientBirthDate → 1900-01-01**,
- sets **StudyDate → 1900-01-01 + the patient's age** at that study (so the age
  is exactly preserved), and
- shifts **SeriesDate / AcquisitionDate / ContentDate** by the same offset (so
  intervals within the study are preserved).

It reads the original dates *before* the Basic Profile empties them, so it works
with the Basic Profile on. A patient's multiple studies keep their correct
relative ages. (Don't combine it with **retain study dates**, which does the
opposite — keep the real dates.)

---

## 5. Morphing (per-file values from a crosswalk)

**Replace** sets *one fixed value for every file*. **Morphing** is different: it
sets **different values per object**, looked up from a crosswalk — e.g. assign
each accession its own pseudonymous name/ID.

Tick **Enable morphing**, then:

1. **Key tag (lookup)** — pick the tag whose value identifies each record
   (e.g. *Accession Number*).
2. **Overwrite tags** — pick a tag and **+ Add** it; repeat for as many tags as
   you want to set. The "→ will set" line lists them. **Yes, you can morph many
   tags at once.**
3. **Crosswalk file (CSV)** — a CSV whose columns, in order, are: the **key
   value**, then one column per overwrite tag (same order as above). Leave
   **has header row** ticked if the first row is a header.

**Example:** Key tag = *Accession Number*, Overwrite tags = *Patient Name*,
*Patient ID*, with this CSV:

```
accession,name,id
ACC123,CASE-001,ID-001
ACC124,CASE-002,ID-002
```

An object whose `AccessionNumber` is `ACC123` gets `PatientName=CASE-001` and
`PatientID=ID-001`. A key not in the file is left unchanged by morphing.

> Morphing runs *before* the Basic Profile would blank those tags, so the
> morphed values are what remains. New tags it creates use VR `LO`. (A legacy
> pipe-delimited `|` file is still accepted.)

**Can morphing be part of a saved profile?** Not yet — profiles hold fixed
per-tag rules, while morphing needs an external crosswalk file, so it stays a
separate option on the tool. (If you want morphing settings saved into profiles,
that's a planned enhancement — ask and it can be added.)

---

## 6. The anonymization pipeline (de-id → send)

To de-identify and push straight to a PACS/VNA in one step:

1. Configure the run (profile, date shift, etc.) and an **Output folder**.
2. Tick **Then C-STORE output to:** and pick a **PACS / Storage** destination
   (add it in **Settings** first).
3. Click **De-identify + Send**.

It writes the de-identified copies to the output folder, then C-STOREs exactly
those files to the destination and reports `sent / failed`. The output folder
still holds the copies, so you have a local record.

---

## 7. Recommended recipes

- **Share a trial cohort:** Basic Profile **on**, **Shift dates** on, add
  **Replace** `PatientID` with your cohort code, **Keep** `StudyDescription` /
  `BodyPartExamined`. Pipe to the research VNA.
- **Re-identify from a crosswalk:** Basic Profile **on**, **Keep** nothing,
  **Morphing** mapping accession → your `PatientName` / `PatientID`.
- **Catch burned-in PHI:** after de-id, open **Preview** or run **QA → Render**;
  use **Remove Image Top/Left %** for modalities with burned-in banners.
