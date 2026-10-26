"""'My data' dialog: profiles, stored values, custom fields and suggestions."""

from __future__ import annotations

import copy
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from scan2form.core.autofill.importer import import_entries, parse_entries
from scan2form.core.autofill.keys import CATALOGUE, MARITAL_CHOICES, parse_date, resolve
from scan2form.core.autofill.mapping import accept_suggestion, add_custom_key, dismiss_suggestion
from scan2form.core.autofill.vault import Profile, Vault, VaultStore

KEY_ROLE = Qt.ItemDataRole.UserRole
to_iso_date = parse_date


def from_iso_date(text: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    return f"{m[3]}.{m[2]}.{m[1]}" if m else text


class VaultDialog(QDialog):
    """Edits a copy of the vault; `accept()` saves it encrypted, Cancel discards everything."""

    def __init__(self, store: VaultStore, vault: Vault, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My data")
        self.resize(720, 760)
        self.store = store
        self.vault = copy.deepcopy(vault)
        self._current: Profile | None = None

        self.profile_box = QComboBox()
        self.profile_box.currentIndexChanged.connect(self._switch_profile)
        new_profile = QPushButton("New profile…")
        new_profile.clicked.connect(self._new_profile)
        rename = QPushButton("Rename…")
        rename.clicked.connect(self._rename_profile)
        self.delete_profile = QPushButton("Delete")
        self.delete_profile.clicked.connect(self._delete_profile)
        top = QHBoxLayout()
        top.addWidget(QLabel("Profile:"))
        top.addWidget(self.profile_box, 1)
        top.addWidget(new_profile)
        top.addWidget(rename)
        top.addWidget(self.delete_profile)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Group", "Field", "Value"])
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        add_custom = QPushButton("Add custom field…")
        add_custom.clicked.connect(self._add_custom)
        self.remove_custom = QPushButton("Remove custom field")
        self.remove_custom.clicked.connect(self._remove_custom)
        custom_row = QHBoxLayout()
        custom_row.addWidget(add_custom)
        custom_row.addWidget(self.remove_custom)
        custom_row.addStretch(1)
        import_file = QPushButton("Import from file…")
        import_file.setToolTip("Read 'Label: value' lines (text or Markdown) into this profile")
        import_file.clicked.connect(self._import_file)
        custom_row.addWidget(import_file)

        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(130)
        accept = QPushButton("Add as field")
        accept.clicked.connect(self._accept_suggestion)
        dismiss = QPushButton("Ignore")
        dismiss.clicked.connect(self._dismiss_suggestion)
        suggestion_buttons = QHBoxLayout()
        suggestion_buttons.addWidget(accept)
        suggestion_buttons.addWidget(dismiss)
        suggestion_buttons.addStretch(1)
        suggestion_box = QGroupBox(
            "Suggestions from your forms (fields with a label that no data matches yet)"
        )
        suggestion_layout = QVBoxLayout(suggestion_box)
        suggestion_layout.addWidget(self.suggestions)
        suggestion_layout.addLayout(suggestion_buttons)

        note = QLabel(
            "Stored encrypted on this PC only, unlocked by your Windows login. "
            "Dates as TT.MM.JJJJ. Combined values (full name, PLZ + Ort, Ort, Datum) are computed."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(custom_row)
        layout.addWidget(suggestion_box)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self._reload_profiles(self.vault.profiles[0].id)
        self._reload_suggestions()

    # -- table ------------------------------------------------------------------
    def _rows(self):
        for key_def in CATALOGUE:
            if not key_def.derived:
                yield key_def.group, key_def.key, key_def.label, key_def.kind
        for custom in self.vault.custom_keys:
            yield "Eigene", custom.key, custom.label, "text"

    def _fill_table(self, profile: Profile) -> None:
        self.table.setRowCount(0)
        for group, key, label, kind in self._rows():
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in ((0, group), (1, label)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(KEY_ROLE, key)
                self.table.setItem(row, col, item)
            value = profile.values.get(key, "")
            if kind == "choice":
                combo = QComboBox()
                combo.addItem("—", "")
                for choice in MARITAL_CHOICES:
                    combo.addItem(choice.replace("_", " "), choice)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                self.table.setCellWidget(row, 2, combo)
            else:
                item = QTableWidgetItem(from_iso_date(value) if kind == "date" else value)
                item.setData(KEY_ROLE, kind)
                self.table.setItem(row, 2, item)
        # Computed values, read-only, so it is visible what e.g. "Ort, Datum" will be.
        grey = QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText))
        for key_def in CATALOGUE:
            if not key_def.derived:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            preview = resolve(key_def.key, profile.values)
            for col, text in (
                (0, key_def.group),
                (1, key_def.label),
                (2, f"{preview}   (computed)"),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setForeground(grey)
                item.setData(KEY_ROLE, key_def.key if col < 2 else "derived")
                self.table.setItem(row, col, item)

    def _commit_table(self) -> None:
        """Copy the table into the current profile (before switching profile or saving)."""
        if self._current is None:
            return
        values: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 1).data(KEY_ROLE)
            combo = self.table.cellWidget(row, 2)
            if isinstance(combo, QComboBox):
                value = combo.currentData() or ""
            else:
                item = self.table.item(row, 2)
                if item is not None and item.data(KEY_ROLE) == "derived":
                    continue
                value = item.text().strip() if item else ""
                if item is not None and item.data(KEY_ROLE) == "date":
                    value = to_iso_date(value)
            if value:
                values[key] = value
        self._current.values = values

    # -- profiles ----------------------------------------------------------------
    def _reload_profiles(self, select_id: str) -> None:
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for profile in self.vault.profiles:
            self.profile_box.addItem(profile.name, profile.id)
        self.profile_box.setCurrentIndex(self.profile_box.findData(select_id))
        self.profile_box.blockSignals(False)
        self._current = self.vault.profile(select_id)
        self._fill_table(self._current)
        self.delete_profile.setEnabled(len(self.vault.profiles) > 1)

    def _switch_profile(self, _index: int) -> None:
        self._commit_table()
        self._reload_profiles(self.profile_box.currentData())

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Name (e.g. Partner, Kind, Firma):")
        if ok and name.strip():
            self._commit_table()
            profile = Profile(name.strip())
            self.vault.profiles.append(profile)
            self._reload_profiles(profile.id)

    def _rename_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Rename profile", "Name:", text=self._current.name)
        if ok and name.strip():
            self._commit_table()
            self._current.name = name.strip()
            self._reload_profiles(self._current.id)

    def _delete_profile(self) -> None:
        if len(self.vault.profiles) <= 1:
            return
        answer = QMessageBox.question(
            self, "Delete profile", f"Delete “{self._current.name}” and all its data?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.vault.profiles.remove(self._current)
            self._current = None
            self._reload_profiles(self.vault.profiles[0].id)

    # -- custom fields & suggestions -------------------------------------------------
    def _add_custom(self, label: str | None = None) -> None:
        if not label:
            label, ok = QInputDialog.getText(
                self, "Custom field", "Field name (e.g. Kundennummer):"
            )
            if not ok or not label.strip():
                return
        self._commit_table()
        add_custom_key(self.vault, label)
        self._fill_table(self._current)

    def _remove_custom(self) -> None:
        row = self.table.currentRow()
        key = self.table.item(row, 1).data(KEY_ROLE) if row >= 0 else None
        custom = self.vault.custom_key(key) if key else None
        if custom is None:
            QMessageBox.information(
                self, "Remove custom field", "Select one of your own fields (group “Eigene”)."
            )
            return
        self._commit_table()
        self.vault.custom_keys.remove(custom)
        for profile in self.vault.profiles:
            profile.values.pop(custom.key, None)
        self.vault.learned = {k: v for k, v in self.vault.learned.items() if v != custom.key}
        self._fill_table(self._current)

    def _import_file(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import data", "", "Text / Markdown (*.md *.txt);;All files (*)"
            )
            if not path:
                return
        try:
            with open(path, encoding="utf-8") as fh:
                entries = parse_entries(fh.read())
        except (OSError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, "Import data", f"Could not read the file:\n{exc}")
            return
        self._commit_table()
        result = import_entries(self.vault, self._current, entries)
        self._fill_table(self._current)
        lines = [f"{len(result.stored)} values imported into “{self._current.name}”."]
        if result.created:
            lines.append(f"New custom fields: {', '.join(result.created)}")
        lines += [f"Skipped “{label}”: {reason}" for label, reason in result.skipped]
        lines.append("Check the table, then press Save.")
        QMessageBox.information(self, "Import data", "\n".join(lines))

    def _reload_suggestions(self) -> None:
        self.suggestions.clear()
        for s in sorted(self.vault.suggestions, key=lambda s: -s.count):
            forms = "1 form" if s.count == 1 else f"{s.count} forms"
            item = QListWidgetItem(f"{s.label}   (seen in {forms})")
            item.setData(KEY_ROLE, s.slug)
            self.suggestions.addItem(item)

    def _selected_suggestion(self) -> str | None:
        item = self.suggestions.currentItem()
        return item.data(KEY_ROLE) if item else None

    def _accept_suggestion(self) -> None:
        slug = self._selected_suggestion()
        if slug:
            self._commit_table()
            accept_suggestion(self.vault, slug)
            self._fill_table(self._current)
            self._reload_suggestions()

    def _dismiss_suggestion(self) -> None:
        slug = self._selected_suggestion()
        if slug:
            dismiss_suggestion(self.vault, slug)
            self._reload_suggestions()

    # -- result ----------------------------------------------------------------------
    def accept(self) -> None:
        self._commit_table()
        self.store.save(self.vault)
        super().accept()
