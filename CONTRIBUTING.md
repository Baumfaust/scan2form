# Contributing to Scan2Form

Thanks for helping! Bug reports, ideas and pull requests are welcome.

## Privacy first: never share real forms

Forms people fill out contain personal data. **Do not attach real or filled-in forms,
screenshots of them, or your `vault.s2fv`** to issues or pull requests — not even "just the
blank page". Reproduce problems with a made-up form instead (`tools/make_screenshots.py` shows
how to draw one with reportlab) and describe the layout in words. Put real test forms only into
`local_tests/`, which is gitignored and must stay on your machine.

## Development setup

```sh
git clone https://github.com/Baumfaust/scan2form.git
cd scan2form
uv sync                     # Python 3.12 + all dependencies + dev tools
uv run scan2form-gui        # start the app
uv run pytest               # all tests (Qt runs offscreen)
uv run ruff check src tests tools
uv run ruff format src tests tools
```

Windows package: `uv sync --group build` and `uv run python tools/build_windows.py`
(builds `dist/`, runs the packaged app's self-test, zips it).

## Guidelines

- Keep it **offline**: no network calls at runtime, no telemetry; third-party libraries that
  phone home must have it disabled.
- **No AGPL dependencies** in the shipped app; new dependencies need a GPL-3.0-compatible license.
- Core logic goes into `src/scan2form/core` (no Qt), the UI into `src/scan2form/ui`. Coordinates
  are always view points (see `core/coords.py`).
- Add or adjust tests for what you change; CI runs ruff and pytest on Windows and Linux.
- Match the style of the surrounding code; `ruff format` decides formatting.
- One topic per pull request, with a short description of what and why.

## License of contributions

Scan2Form is licensed under GPL-3.0-or-later. By submitting a pull request you agree that your
contribution is licensed under GPL-3.0-or-later **and** that the maintainer may also distribute
it under other license terms (for example a commercial license for a paid edition), so the
project can be offered under more than one license. You keep the copyright to your work.
If you cannot agree to this, please open an issue first.

## Reporting security problems

For anything that could expose personal data (e.g. the encrypted "My data" vault), please do not
open a public issue — use GitHub's *Report a vulnerability* (Security tab) instead.
