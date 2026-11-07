# Security Policy

DICOM Toolkit is an offline Windows desktop app used in clinical imaging
workflows. Because it handles DICOM data, which can contain protected health
information (PHI), we take security and privacy issues seriously.

## Supported versions

Only the latest released version receives security fixes. The app self-updates
on launch (HTTPS + SHA-256 verified), so please update to the newest
[release](https://github.com/towbinaj/DCMTools/releases) before reporting.

| Version        | Supported |
| -------------- | --------- |
| Latest release | ✅        |
| Older releases | ❌        |

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do **not** open a public
GitHub issue or pull request, and do not disclose the issue publicly until a
fix has shipped.

- **Email:** DICOMTool@gmail.com
- Or use GitHub's private vulnerability reporting: the **"Report a
  vulnerability"** button on the repository's **Security** tab.

Please include:

- the app version (shown in Settings; the built-in "Report a Bug" also includes
  it),
- the affected tab/feature and your Windows version,
- steps to reproduce and the security impact,
- any relevant, **de-identified** logs or screenshots.

### Do not include patient data

This tool processes DICOM objects, which routinely contain PHI. **Never** put
real patient data or real site identifiers in a security report — including
patient names, MRNs, accession numbers, AE titles, hostnames, or IP addresses.
Use synthetic or placeholder values (e.g. `TESTPACS`, `192.0.2.1`,
`example.org`). If a reproduction appears to require real data, describe it
instead and we will follow up on how to share details safely.

## What to expect

- We aim to acknowledge reports within **5 business days**.
- We will keep you informed as we investigate, and — with your permission —
  credit you when a fix is released.

## Design notes relevant to security

- The app runs **fully offline**; its only self-initiated network calls are
  HTTPS **GET** requests to GitHub to check for and download updates, which are
  SHA-256 verified before the executable is swapped in place.
- The bundled OHIF viewer's web server binds to **127.0.0.1 only**.
- The public repository is kept **secret-free**: real destinations, credentials,
  and PHI live only in the local, git-ignored `DICOMToolkit-data/`.
