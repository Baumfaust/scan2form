"""Screenshots for the README, made from a fictitious demo form with fake data.

    uv run python tools/make_screenshots.py   ->  docs/screenshots/prepare.png, fill.png

Runs offscreen with a temporary vault and an in-memory keyring, so the user's real
data and credential store are never touched. Never use real forms here.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import keyring
from keyring.backend import KeyringBackend
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
PROFILE = {
    "first_name": "Erika", "last_name": "Mustermann", "birth_date": "1964-08-12",
    "street": "Heidestraße", "house_number": "17", "postcode": "51147", "city": "Köln",
    "phone": "0221 1234567", "email": "erika@example.org", "marital_status": "verheiratet",
    "iban": "DE02 1203 0000 0000 2020 51", "bic": "BYLADEM1001", "bank": "Musterbank",
}  # fmt: skip


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def draw_demo_form(path: Path) -> Path:
    """A two-page 'Mitgliedsantrag' with boxes, lines, checkboxes and signatures."""
    c = canvas.Canvas(str(path), pagesize=A4)

    def heading(text: str, y: float) -> None:
        c.setFont("Helvetica-Bold", 15)
        c.drawString(72, y, text)
        c.setFont("Helvetica", 11)

    def box(label: str, x: float, y: float, w: float, h: float = 20) -> None:
        c.drawRightString(x - 8, y + 6, label)
        c.rect(x, y, w, h)

    def line(label: str, x: float, y: float, w: float) -> None:
        c.drawString(x, y - 13, label)
        c.line(x, y, x + w, y)

    heading("Mitgliedsantrag – Musterverein e.V.", 780)
    c.drawString(72, 750, "Angaben zur Person")
    box("Vorname:", 190, 710, 300)
    box("Nachname:", 190, 680, 300)
    box("Geburtsdatum:", 190, 650, 140)
    box("Straße und Hausnummer:", 190, 620, 300)
    box("PLZ / Ort:", 190, 590, 300)
    box("Telefon:", 190, 560, 140)
    box("E-Mail:", 190, 530, 300)
    c.drawString(72, 495, "Familienstand:")
    for i, label in enumerate(("ledig", "verheiratet", "geschieden")):
        x = 190 + 100 * i
        c.rect(x, 493, 11, 11)
        c.drawString(x + 16, 495, label)
    box("Bemerkung:", 190, 380, 300, 80)
    line("Ort, Datum", 72, 300, 180)
    line("Unterschrift", 300, 300, 200)
    c.showPage()

    c.setFont("Helvetica", 11)
    heading("SEPA-Lastschriftmandat", 780)
    box("Vorname:", 190, 730, 300)
    box("Nachname:", 190, 700, 300)
    box("Kontoinhaber:", 190, 670, 300)
    box("IBAN:", 190, 640, 300)
    box("BIC:", 190, 610, 140)
    box("Kreditinstitut:", 190, 580, 300)
    line("Ort, Datum", 72, 500, 180)
    line("Unterschrift", 300, 500, 200)
    c.showPage()
    c.save()
    return path


def main() -> None:
    keyring.set_keyring(MemoryKeyring())
    tmp = Path(tempfile.mkdtemp(prefix="scan2form-shots-"))
    import scan2form.core.autofill.vault as vault_module

    vault_module.default_vault_path = lambda: tmp / "vault.s2fv"

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from scan2form.core.autofill.vault import Profile, Vault
    from scan2form.core.detect import OpenCVDetector
    from scan2form.core.ocr import Ocr
    from scan2form.core.pipeline import process
    from scan2form.ui import main_window
    from scan2form.ui.app import app_icon

    main_window.settings = lambda: QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat)
    app.setWindowIcon(app_icon())
    form = draw_demo_form(tmp / "Mitgliedsantrag.pdf")
    document = process(form, OpenCVDetector(), Ocr())

    window = main_window.MainWindow()
    window.resize(1440, 900)
    window.vault_store.save(Vault([Profile("Ich", PROFILE)]))
    window.show()
    window.set_document(document)
    app.processEvents()
    OUT.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(OUT / "prepare.png"))

    window.set_mode("fill")
    app.processEvents()
    window.grab().save(str(OUT / "fill.png"))
    window.fill_session.dirty = False  # nothing to keep: fake data
    window.close()
    print(f"{document.field_count} fields detected; screenshots in {OUT}")


if __name__ == "__main__":
    main()
