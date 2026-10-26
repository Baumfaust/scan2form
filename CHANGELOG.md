# Changelog

All notable changes to Scan2Form. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-26

First public preview. Windows, German and English forms.

**Download:** `Scan2Form-0.1.0-windows-x64.zip` below — unzip anywhere and start `Scan2Form.exe`.
No installation, no account, works completely offline (the OCR models are included).
Windows may warn that the app is from an unknown publisher (it is not code-signed yet):
choose *More info → Run anyway*. Compare the zip with its `.sha256` file if you like.

### Added
- **① Prepare form:** detects text fields, boxes, checkboxes and signature lines on scans and
  PDFs, and names them from the printed labels (local OCR, German/English).
- Automatic straightening of crooked scans; 90° turns and ± 10° fine-tuning with a grid.
- Review canvas: brush that snaps to printed fields, exact boxes, make uniform / align,
  undo/redo, names inside the boxes, colour key.
- Export of standard fillable PDFs (AcroForms); fields with the same label on several pages
  become one linked field; radio groups.
- **② Fill out:** type directly on the page; pre-filled from *My data*; signature dates get
  today's date; values you type are never overwritten; encrypted drafts so nothing is lost;
  saves a separate `*.filled.pdf`. Also fills third-party fillable PDFs.
- **My data:** profiles stored AES-256-GCM encrypted, unlocked by your Windows login;
  learns new data from what you type; import from a text/Markdown list.
- Open Recent, Close, command-line tool `scan2form`.
