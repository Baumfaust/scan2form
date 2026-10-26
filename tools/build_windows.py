"""Build the Windows app: dist/Scan2Form-<version>-windows-x64.zip (+ .sha256).

    uv sync --group build
    uv run python tools/build_windows.py

1. stages the three OCR models (RapidOCR downloads them once if missing) so the app
   never needs a connection,
2. collects the license texts of every bundled package into licenses/,
3. runs PyInstaller (packaging/scan2form.spec, one folder, no console),
4. runs the built app's --self-test (fails the build if anything is missing),
5. zips the folder and writes its SHA-256.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
BUILD, DIST = ROOT / "build", ROOT / "dist"
APP = DIST / "Scan2Form"
LICENSE_FILE = re.compile(r"(licen[cs]e|copying|notice|authors)", re.IGNORECASE)


def stage_models() -> None:
    from scan2form.core.ocr import MODEL_FILES, Ocr

    Ocr()._get_engine()  # development install: downloads missing models once
    source = Path(__import__("rapidocr").__file__).parent / "models"
    target = BUILD / "ocr-models"
    target.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES.values():
        shutil.copy2(source / name, target / name)
    print(f"staged {len(MODEL_FILES)} OCR models")


def runtime_distributions() -> list[str]:
    """scan2form's runtime dependencies, recursively (markers evaluated for this platform)."""
    seen: dict[str, None] = {}
    todo = ["scan2form"]
    while todo:
        name = todo.pop()
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        key = dist.metadata["Name"].lower()
        if key in seen:
            continue
        seen[key] = None
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    return sorted(n for n in seen if n != "scan2form")


def write_licenses(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", target / "Scan2Form-GPL-3.0.txt")
    index = ["Scan2Form is licensed under GPL-3.0-or-later (Scan2Form-GPL-3.0.txt).",
             "Source code: https://github.com/Baumfaust/scan2form", "",
             "Bundled third-party packages and their licenses:"]  # fmt: skip
    for name in runtime_distributions():
        dist = distribution(name)
        meta = dist.metadata
        lic = meta.get("License-Expression") or meta.get("License") or "see files"
        index.append(f"- {meta['Name']} {meta['Version']}: {lic.splitlines()[0][:80]}")
        for file in dist.files or []:
            if LICENSE_FILE.search(file.name) and file.suffix.lower() in (
                "",
                ".txt",
                ".md",
                ".rst",
            ):
                text = Path(file.locate()).read_text(encoding="utf-8", errors="replace")
                (target / f"{meta['Name']}-{file.name}").write_text(text, encoding="utf-8")
    index += ["", "- PP-OCRv5 models (PaddleOCR, via RapidOCR): Apache-2.0",
              "- Qt (via PySide6): LGPL-3.0. The Qt libraries are separate files in this folder",
              "  and may be replaced with compatible versions."]  # fmt: skip
    (target / "README.txt").write_text("\n".join(index) + "\n", encoding="utf-8")


def self_test() -> None:
    report = BUILD / "self-test.txt"
    result = subprocess.run(
        [str(APP / "Scan2Form.exe"), "--self-test", str(report)], check=False, timeout=300
    )
    print(report.read_text(encoding="utf-8") if report.exists() else "no report written")
    if result.returncode != 0:
        sys.exit(f"self-test failed (exit {result.returncode})")


def make_zip() -> Path:
    archive = DIST / f"Scan2Form-{version('scan2form')}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(APP.rglob("*")):
            zf.write(path, Path("Scan2Form") / path.relative_to(APP))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"{archive.name}: {archive.stat().st_size / 1e6:.1f} MB, sha256 {digest}")
    return archive


def main() -> None:
    if sys.platform != "win32":
        sys.exit("The Windows build must run on Windows.")
    shutil.rmtree(APP, ignore_errors=True)
    stage_models()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "packaging" / "scan2form.spec")], check=True)  # fmt: skip
    write_licenses(APP / "licenses")
    self_test()
    make_zip()


if __name__ == "__main__":
    main()
