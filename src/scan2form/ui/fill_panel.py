"""Fill mode side panel: which stored datum fills which field (values are typed on the page)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from scan2form.core.autofill.keys import MARITAL_CHOICES
from scan2form.core.autofill.mapping import FormField, all_keys
from scan2form.core.autofill.session import FillSession

COL_FIELD, COL_MAP = range(2)
NOT_FILLED = ""


class FillPanel(QWidget):
    mappingChanged = Signal(str)  # field id; its value was recomputed
    fieldActivated = Signal(str)  # field id the user picked in the list

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.session: FillSession | None = None
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Field", "Filled from"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_FIELD, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_MAP, QHeaderView.ResizeMode.Stretch)
        self.table.cellClicked.connect(self._row_clicked)
        note = QLabel(
            "Type directly into the green boxes on the page; Tab / Enter jumps to the next field. "
            "What you type is kept as an encrypted draft until you save the copy, and new data "
            "for labelled fields goes into My data (changes to existing data are asked first). "
            "The filled copy contains your data in plain text; the form itself is not changed."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)

    def set_session(self, session: FillSession) -> None:
        self.session = session
        self.table.setRowCount(0)
        for f in session.fields:
            self._add_row(f)
        self.update_summary()

    def _options(self, field: FormField) -> list[tuple[str, str]]:
        options = [(NOT_FILLED, "— not filled —")]
        options += all_keys(self.session.vault)
        if field.kind == "checkbox":
            options += [(f"marital_status={c}", f"Familienstand = {c.replace('_', ' ')}")
                        for c in MARITAL_CHOICES]  # fmt: skip
        return options

    def _add_row(self, field: FormField) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        label = field.label if field.label == field.name else f"{field.label}  [{field.name}]"
        item = QTableWidgetItem(label)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setData(Qt.ItemDataRole.UserRole, field.id)
        item.setToolTip(field.id)
        self.table.setItem(row, COL_FIELD, item)
        combo = QComboBox()
        for code, text in self._options(field):
            combo.addItem(text, code)
        mapping = self.session.mappings.get(field.id)
        combo.setCurrentIndex(max(0, combo.findData(mapping.encode() if mapping else NOT_FILLED)))
        combo.currentIndexChanged.connect(lambda _i, fid=field.id, c=combo: self._remap(fid, c))
        self.table.setCellWidget(row, COL_MAP, combo)

    def row_of(self, field_id: str) -> int:
        for row in range(self.table.rowCount()):
            if self.table.item(row, COL_FIELD).data(Qt.ItemDataRole.UserRole) == field_id:
                return row
        return -1

    def _remap(self, field_id: str, combo: QComboBox) -> None:
        self.session.set_mapping(field_id, combo.currentData() or None)
        self.update_summary()
        self.mappingChanged.emit(field_id)

    def _row_clicked(self, row: int, _col: int) -> None:
        self.fieldActivated.emit(self.table.item(row, COL_FIELD).data(Qt.ItemDataRole.UserRole))

    def select_field(self, field_id: str) -> None:
        row = self.row_of(field_id)
        if row >= 0:
            self.table.blockSignals(True)
            self.table.selectRow(row)
            self.table.blockSignals(False)
            self.table.scrollToItem(self.table.item(row, COL_FIELD))

    def update_summary(self) -> None:
        s = self.session
        if s is None:
            return
        mapped = sum(m is not None for m in s.mappings.values())
        filled = len(s.values)
        self.summary.setText(
            f"<b>{filled} of {len(s.fields)}</b> fields filled · {mapped} come from your data "
            f"(profile “{s.profile.name}”) · {s.kept} entered by you, never overwritten. "
            "Change “Filled from” to correct a match; Scan2Form remembers it when you save the copy."
        )

    def sync_mapping(self, field_id: str) -> None:
        """Show a mapping that changed elsewhere (learned from typing)."""
        row = self.row_of(field_id)
        combo = self.table.cellWidget(row, COL_MAP) if row >= 0 else None
        mapping = self.session.mappings.get(field_id)
        if combo is not None:
            code = mapping.encode() if mapping else NOT_FILLED
            if combo.findData(code) < 0:  # a custom field created just now
                label = next(
                    (c.label for c in self.session.vault.custom_keys if c.key == code), code
                )
                combo.addItem(label, code)
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(code))
            combo.blockSignals(False)
        self.update_summary()
