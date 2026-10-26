<div align="center">

<img src="src/scan2form/ui/assets/icon-128.png" width="96" alt="Scan2Form icon">

# Scan2Form

**Turn paper forms into fillable PDFs — and fill them out from your own data.**<br>
100 % offline · no cloud · no account · no telemetry

[![Latest release](https://img.shields.io/github/v/release/Baumfaust/scan2form?label=download&color=2ea44f)](https://github.com/Baumfaust/scan2form/releases/latest)
[![CI](https://github.com/Baumfaust/scan2form/actions/workflows/ci.yml/badge.svg)](https://github.com/Baumfaust/scan2form/actions/workflows/ci.yml)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4)](https://github.com/Baumfaust/scan2form/releases/latest)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

[**Download for Windows**](https://github.com/Baumfaust/scan2form/releases/latest) ·
[How it works](#how-it-works) ·
[Privacy](#your-data-stays-yours) ·
[Changelog](CHANGELOG.md)

</div>

<br>

| Step 1 · Prepare | Step 2 · Fill out |
|:---:|:---:|
| ![Prepare: fields detected and named from their printed labels](docs/screenshots/prepare.png) | ![Fill out: pre-filled from your encrypted data](docs/screenshots/fill.png) |
| Fields are found and named from their printed labels | Type on the page, pre-filled from your own data |

<p align="center"><sub>Screenshots show a fictitious demo form with made-up data.</sub></p>

## Features

- 🔍 **Finds the fields** on scans and PDFs — text lines, boxes, checkboxes, signature lines — and
  **names them from the printed labels** (German and English OCR, running on your PC).
- 📐 **Straightens crooked scans** automatically, with manual fine-tuning.
- ✏️ **Quick corrections:** paint roughly over a missing field and it snaps to the printed line or box.
- 📄 **Exports a standard fillable PDF** that works in every PDF viewer.
- ✍️ **Fills out any fillable PDF** — also forms from authorities or banks — from an encrypted
  store of your personal data, and saves a separate filled copy.
- 🔒 **Private by design:** nothing ever leaves your computer.

## Download

1. Download `Scan2Form-<version>-windows-x64.zip` from the
   [latest release](https://github.com/Baumfaust/scan2form/releases/latest).
2. Unzip it anywhere and start **`Scan2Form.exe`**. No installation needed; the OCR models are
   included, so it works without an internet connection.

> [!NOTE]
> The app is not code-signed yet, so Windows may show *"Windows protected your PC"*.
> Click **More info → Run anyway**. Each release lists the zip's SHA-256 checksum for verification.

## How it works

Scan2Form has two modes. The big buttons top left switch between them
(<kbd>Ctrl</kbd>+<kbd>1</kbd> / <kbd>Ctrl</kbd>+<kbd>2</kbd>); a coloured banner, frame and the
window title always show which one is active.

### Step 1 · Prepare the form (blue)

Open or drag in a PDF or scanned image (PNG, JPG, TIFF …). Fields are detected and named
automatically; each name is shown inside its box and the colour shows the type:

| Colour | Meaning |
|---|---|
| 🟦 Blue | Text field |
| 🟩 Green | Checkbox |
| 🟪 Purple | Signature — signed by hand, never filled automatically |
| 🟧 Orange, dashed | Uncertain detection, worth a check |

Then fix what needs fixing and **export a fillable PDF** (<kbd>Ctrl</kbd>+<kbd>E</kbd>).

<details>
<summary><b>Tools and shortcuts</b></summary>

| Key | Tool / action |
|---|---|
| <kbd>V</kbd> | **Select:** drag to move, handles to resize, arrow keys nudge (<kbd>Shift</kbd> = more), <kbd>Del</kbd> deletes |
| <kbd>B</kbd> | **Brush:** paint roughly over a missing field — it snaps to the printed line, box or checkbox. A single click on a checkbox adds it |
| <kbd>R</kbd> | **Box:** draw an exact rectangle |
| <kbd>U</kbd> | **Make uniform:** select several fields (drag a frame around them or <kbd>Ctrl</kbd>+click), then give them the same size and align them. More in the *Arrange* menu |
| <kbd>G</kbd> | **Grid** (10 mm) to check the straightening |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd> | Undo / redo |
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | Save the project (`*.s2f.json`) |
| <kbd>Ctrl</kbd>+<kbd>E</kbd> | Export the fillable PDF (`*.fillable.pdf`) — the original is never overwritten |

</details>

<details>
<summary><b>Names, linked fields and crooked scans</b></summary>

- **Names:** change a field's name in the *Properties* panel. Fields with the same label on several
  pages ("Vorname" on pages 1–3) are **one linked field**: filled once, shown everywhere. Give a text
  field the name of another to link them, a different name to unlink. *Edit → Link repeated fields
  across pages* merges older `vorname_2`, `vorname_3` ….
- **Crooked or sideways scans** are straightened automatically. The *Page* toolbar turns a page by
  90° (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>←</kbd> / <kbd>→</kbd>), fine-tunes the angle (± 10°,
  with the grid), measures it again (*Auto*) or applies the orientation to all pages. Fields move
  along. Scanned pages are exported straightened at their original resolution; digital PDFs are never
  rasterised and only turn in lossless 90° steps.

</details>

### Step 2 · Fill it out (green)

Type directly into the green boxes on the page — <kbd>Tab</kbd> or <kbd>Enter</kbd> jumps to the
next field. Fields are pre-filled from *My data*, signature dates with today's date. Then
**save a filled copy** (<kbd>Ctrl</kbd>+<kbd>S</kbd>); the blank form is never changed.

- What you type is **never overwritten** by *Fill from profile*.
- **Nothing gets lost:** your entries are kept as an encrypted draft until the copy is saved, and
  come back when you open the form again — even after a crash.
- **Already fillable PDFs** (also third-party forms) open straight in this mode with their own fields.
  As long as you don't change them, the original form is filled as it is.
- All filled text uses one font size (10 pt on paper) and only shrinks where a value would not fit.

<details>
<summary><b>Shortcuts</b></summary>

| Key | Action |
|---|---|
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | Save the filled copy (`*.filled.pdf`); afterwards it updates the same copy |
| <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd> | Save the filled copy as … |
| <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> | Fill from the profile selected in the toolbar |

</details>

### Files at a glance

| File | Saved with | Contains |
|---|---|---|
| `form.s2f.json` | *Save project* (Step 1) | Field layout, names, page orientation — no personal data |
| `form.fillable.pdf` | *Export fillable PDF* (Step 1) | The blank form with fields |
| `form.filled.pdf` | *Save filled copy* (Step 2) | The form with your data |

*File → Open Recent* lists the last 10 forms and projects; *File → Close*
(<kbd>Ctrl</kbd>+<kbd>W</kbd>) returns to the start screen.

## Your data stays yours

**Tools → My data** stores your personal data — name, address, birth date, bank details and
your own custom fields — in profiles such as *Ich*, *Partner* or *Kind*.

> [!IMPORTANT]
> Everything is stored **AES-256-GCM encrypted** in `%LOCALAPPDATA%\Scan2Form\vault.s2fv`. The key
> lives in the Windows Credential Manager, so the data opens with your Windows login only.
> Nothing is ever sent anywhere. (A filled PDF contains your data in plain text, like any filled form.)

- **Smart matching:** fields are matched by their label, with German and English synonyms
  ("Geburtsdatum (TT.MM.JJJJ)", "PLZ / Ort", "Ort, Datum" …); combined values such as full name or
  postcode + city are put together automatically.
- **It learns from you:** new data you type into a labelled field is added to *My data*; changes
  to data you already stored are confirmed once when you save. Corrected matches are remembered for
  every form with that label.
- **Import** your data from a simple list (`Vorname: Erika`, also Markdown) via
  *My data → Import from file…*.

## Run from source

Requires [uv](https://docs.astral.sh/uv/), which brings Python 3.12 along.

```sh
git clone https://github.com/Baumfaust/scan2form.git
cd scan2form
uv sync
uv run scan2form-gui
```

From source, the two OCR models (about 13 MB) are downloaded once on the first OCR run; after that
everything runs offline.

<details>
<summary><b>Command line</b></summary>

```sh
uv run scan2form form.pdf -o form.fillable.pdf          # also accepts PNG/JPG/TIFF scans
uv run scan2form form.pdf --no-ocr --project fields.json
uv run python -m scan2form.core.autofill.importer data.md --profile Ich   # import My data
```

Pipeline: render → straighten → detect fields → OCR (German/English) → name from nearby labels →
export AcroForm.

</details>

<details>
<summary><b>Development</b></summary>

```sh
uv sync                                                       # core + dev tools
uv run pytest                                                 # all tests (Qt runs offscreen)
uv run ruff check src tests tools && uv run ruff format --check src tests tools
uv run python tools/make_screenshots.py                       # README screenshots (demo form, fake data)
uv run python tools/make_icon.py                              # app icon (PNGs + .ico)
uv run scan2form-spike <folder-with-forms>                    # compare detectors
```

**Windows package:** `uv sync --group build` and `uv run python tools/build_windows.py` build a
one-folder app with the OCR models, run its `--self-test` and zip it to `dist/`. Pushing a tag
`v*` does the same on GitHub Actions and publishes a release with the matching
[CHANGELOG](CHANGELOG.md) section.

**Optional FFDNet comparison** (local evaluation only, never shipped — Ultralytics is AGPL-3.0 and
the CommonForms/FFDNet weights have no license yet): `uv sync --group ffdnet-eval`, then
`uv run scan2form-spike <folder> -d opencv,ffdnet-s,ffdnet-l`.

Real-world test forms belong in `local_tests/`, which is gitignored and never shared.

</details>

## Contributing

Bug reports, ideas and pull requests are welcome — please read [CONTRIBUTING](CONTRIBUTING.md)
first. **Never attach real forms or personal data** to issues; a made-up example is enough.

## License

Scan2Form is free software under the [GNU General Public License v3.0 or later](LICENSE): you may
use, study, share and change it; modified versions you distribute must stay under the GPL with
their source available.

<details>
<summary><b>Third-party components</b> (all GPL-3.0 compatible)</summary>

| Component | License |
|---|---|
| PySide6 / Qt for Python | LGPL-3.0 (also offered under GPL-2.0/3.0) |
| RapidOCR + PP-OCR models | Apache-2.0 |
| OpenCV (opencv-python-headless) | Apache-2.0 |
| ONNX Runtime | MIT |
| pypdf | BSD-3-Clause |
| pypdfium2 / PDFium | Apache-2.0 / BSD-3-Clause |
| cryptography | Apache-2.0 or BSD-3-Clause |
| keyring, platformdirs | MIT |
| Pillow | MIT-CMU |
| NumPy | BSD-3-Clause |

The Windows download contains every license text in its `licenses` folder. No AGPL code is part of
the app.

</details>
