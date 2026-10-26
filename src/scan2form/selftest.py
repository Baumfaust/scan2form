"""`Scan2Form.exe --self-test [report.txt]`: check a packaged build without opening a window.

Exit code 0 when the bundled parts work: Qt, OpenCV, the PDF libraries, the vault
cryptography and OCR from the bundled models (no download). The report lists each check.
"""

from __future__ import annotations

import sys
from pathlib import Path


def run(report_path: Path | None = None) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"OK    {name}{': ' + detail if detail else ''}")
        except Exception as exc:  # noqa: BLE001 - every failure must end up in the report
            ok = False
            lines.append(f"FAIL  {name}: {type(exc).__name__}: {exc}")

    def qt() -> str:
        from PySide6 import __version__
        from PySide6.QtWidgets import QWidget  # noqa: F401

        return __version__

    def pdf() -> str:
        import pypdf
        import pypdfium2

        return f"pypdf {pypdf.__version__}, pypdfium2 {pypdfium2.version.PYPDFIUM_INFO}"

    def vault_crypto() -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aes, nonce = AESGCM(AESGCM.generate_key(bit_length=256)), b"0" * 12
        if aes.decrypt(nonce, aes.encrypt(nonce, b"x", None), None) != b"x":
            raise RuntimeError("encryption round trip failed")
        return "AES-256-GCM"

    def credential_store() -> str:
        import keyring

        backend = keyring.get_keyring()
        name = type(backend).__name__
        if sys.platform == "win32" and "WinVault" not in name:
            raise RuntimeError(f"Windows Credential Manager not available (got {name})")
        return name

    def opencv() -> str:
        import cv2

        return cv2.__version__

    def ocr() -> str:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        from scan2form.core.model import PageImage
        from scan2form.core.ocr import Ocr, bundled_models_dir

        if getattr(sys, "frozen", False) and bundled_models_dir() is None:
            raise RuntimeError("bundled OCR models missing (would download)")
        image = Image.new("RGB", (640, 120), "white")
        ImageDraw.Draw(image).text((20, 30), "Geburtsdatum Straße", fill="black",
                                   font=ImageFont.load_default(size=40))  # fmt: skip
        texts = [s.text for s in Ocr().read(PageImage(0, np.asarray(image), 2.0, 320, 60))]
        if "Geburtsdatum" not in " ".join(texts):
            raise RuntimeError(f"unexpected OCR result {texts}")
        return f"read {texts}"

    for name, fn in (("Qt", qt), ("PDF", pdf), ("vault crypto", vault_crypto),
                     ("credential store", credential_store), ("OpenCV", opencv),
                     ("OCR (bundled models)", ocr)):  # fmt: skip
        check(name, fn)
    lines.append("PASSED" if ok else "FAILED")
    text = "\n".join(lines)
    if report_path is not None:
        report_path.write_text(text, encoding="utf-8")
    if sys.stdout is not None:  # windowed builds have no console
        print(text)
    return 0 if ok else 1
