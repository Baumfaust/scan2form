import os
from pathlib import Path

# Qt must not need a display in tests; set before pytest-qt creates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import keyring
import pytest
from keyring.backend import KeyringBackend
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# Fields drawn on the synthetic form, in reportlab coordinates (points, bottom-left origin):
# (kind, x, y, width, height, label)
SYNTHETIC_FIELDS = [
    ("box", 150, 700, 250, 20, "Vorname:"),
    ("box", 150, 600, 300, 80, "Bemerkung:"),  # multiline
    ("line", 150, 550, 200, 18, "Straße:"),  # underline field: widget sits on the line
    ("checkbox", 72, 500, 11, 11, "Ledig"),
    ("checkbox", 200, 500, 11, 11, "Verheiratet"),
]


def draw_form(path: Path, widgets: bool) -> Path:
    """Synthetic A4 form; with `widgets` it is fillable (ground truth), else flat."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 11)
    c.setLineWidth(1)
    for i, (kind, x, y, w, h, label) in enumerate(SYNTHETIC_FIELDS):
        if kind == "checkbox":
            c.drawString(x + 16, y + 2, label)
        else:
            c.drawRightString(x - 8, y + 4, label)
        if kind == "line":
            c.line(x, y, x + w, y)
        else:
            c.rect(x, y, w, h)
        if not widgets:
            continue
        if kind == "checkbox":
            c.acroForm.checkbox(name=f"f{i}", x=x, y=y, size=w, borderWidth=0)
        else:
            flags = "multiline" if h > 40 else ""
            c.acroForm.textfield(
                name=f"f{i}", x=x, y=y, width=w, height=h, borderWidth=0, fieldFlags=flags
            )
    c.showPage()
    c.save()
    return path


@pytest.fixture
def synthetic_form(tmp_path: Path) -> Path:
    return draw_form(tmp_path / "synthetic.pdf", widgets=True)


@pytest.fixture
def flat_form(tmp_path: Path) -> Path:
    return draw_form(tmp_path / "flat.pdf", widgets=False)


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
        if self.store.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    """Tests never touch the real Credential Manager or the user's real vault file."""
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    path = tmp_path / "vault" / "vault.s2fv"
    monkeypatch.setattr("scan2form.core.autofill.vault.default_vault_path", lambda: path)
    yield backend
    keyring.set_keyring(previous)
