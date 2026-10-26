"""Side panel to edit the selected field."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from scan2form.core.layout import ARRANGEMENTS
from scan2form.core.model import Field, FieldType
from scan2form.ui.page_view import TYPE_COLORS, UNCERTAIN_COLOR

TYPE_LABELS = {
    FieldType.TEXT: "Text",
    FieldType.CHECKBOX: "Checkbox",
    FieldType.SIGNATURE: "Signature",
}


LEGEND = (
    (TYPE_COLORS[FieldType.TEXT], "solid", "Text field"),
    (TYPE_COLORS[FieldType.CHECKBOX], "solid", "Checkbox"),
    (TYPE_COLORS[FieldType.SIGNATURE], "solid", "Signature (signed by hand, never auto-filled)"),
    (UNCERTAIN_COLOR, "dashed", "Uncertain: please check (low confidence, or nothing to snap to)"),
)


def colour_legend() -> QGroupBox:
    """What the field colours on the page mean; same colours as page_view draws."""
    box = QGroupBox("Colours")
    grid = QGridLayout(box)
    grid.setColumnStretch(1, 1)
    for row, (color, style, text) in enumerate(LEGEND):
        swatch = QLabel()
        swatch.setFixedSize(28, 14)
        swatch.setStyleSheet(
            f"background: rgba({color.red()}, {color.green()}, {color.blue()}, 50); "
            f"border: 2px {style} {color.name()};"
        )
        label = QLabel(text)
        label.setWordWrap(True)
        grid.addWidget(swatch, row, 0)
        grid.addWidget(label, row, 1)
    return box


class PropertiesPanel(QWidget):
    """Emits `changed(field, attrs)`; the window turns that into an undoable command."""

    changed = Signal(object, object)  # object, not dict: QVariantMap would turn enums into str
    deleteRequested = Signal()
    arrangeRequested = Signal(str)  # key of core.layout.ARRANGEMENTS

    def __init__(
        self,
        name_is_taken: Callable[[str, Field], bool],
        linked_pages: Callable[[Field], list[int]] = lambda f: [],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._name_is_taken = name_is_taken
        self._linked_pages = linked_pages
        self._field: Field | None = None
        self._pt_per_mm = 72 / 25.4

        self.heading = QLabel("No field selected")
        self.heading.setWordWrap(True)
        self.name = QLineEdit()
        self.name.setPlaceholderText("field name")
        self.type = QComboBox()
        for field_type, label in TYPE_LABELS.items():
            self.type.addItem(label, field_type.value)  # Qt turns StrEnum into str anyway
        self.multiline = QCheckBox("Multiple lines")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b")
        self.error.setWordWrap(True)
        self.delete = QPushButton("Delete")

        self.arrange_box = QWidget()
        grid = QGridLayout(self.arrange_box)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, (key, (label, _)) in enumerate(ARRANGEMENTS.items()):
            button = QPushButton(label + (" (U)" if key == "uniform" else ""))
            button.clicked.connect(lambda _=False, k=key: self.arrangeRequested.emit(k))
            if key == "uniform":
                grid.addWidget(button, 0, 0, 1, 2)
            else:
                grid.addWidget(button, 1 + (i - 1) // 2, (i - 1) % 2)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        form.addRow("", self.multiline)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(self.info)
        layout.addWidget(self.arrange_box)
        layout.addWidget(self.delete)
        layout.addStretch(1)
        self.legend = colour_legend()
        layout.addWidget(self.legend)

        self.name.editingFinished.connect(self._commit_name)
        self.type.activated.connect(self._commit_type)
        self.multiline.toggled.connect(self._commit_multiline)
        self.delete.clicked.connect(self.deleteRequested)
        self.show_fields([], self._pt_per_mm)

    def show_fields(self, fields: list[Field], pt_per_mm: float) -> None:
        self._pt_per_mm = pt_per_mm
        self._field = fields[0] if len(fields) == 1 else None
        editable = self._field is not None
        for widget in (self.name, self.type, self.multiline):
            widget.setEnabled(editable)
        self.delete.setEnabled(bool(fields))
        self.arrange_box.setVisible(len(fields) >= 2)
        self.error.clear()
        if not editable:
            self.name.clear()
            self.type.setCurrentIndex(-1)
            self.multiline.blockSignals(True)
            self.multiline.setChecked(False)
            self.multiline.blockSignals(False)
        if not fields:
            self.heading.setText("No field selected")
            self.info.setText(
                "Tools: V select · B brush (paint roughly, it snaps) · R box (exact)\n"
                "Arrows nudge, Shift+arrows nudge more, Del deletes.\n"
                "Drag a box around several fields (or Ctrl+click) to arrange them."
            )
        elif not editable:
            self.heading.setText(f"{len(fields)} fields selected")
            self.info.setText("Make them the same size and line them up:")
        if editable:
            self.refresh()

    def refresh(self) -> None:
        f = self._field
        if f is None:
            return
        self.heading.setText("Field")
        self.name.setText(f.name or "")
        self.type.setCurrentIndex(self.type.findData(f.type.value))
        self.multiline.blockSignals(True)
        self.multiline.setChecked(f.multiline)
        self.multiline.blockSignals(False)
        self.multiline.setEnabled(f.type is FieldType.TEXT)
        mm = self._pt_per_mm
        certainty = "uncertain – please check" if f.score <= 0.5 else f"confidence {f.score:.2f}"
        text = f"{f.bbox.width / mm:.1f} × {f.bbox.height / mm:.1f} mm · {certainty}"
        pages = self._linked_pages(f)
        if pages:
            where = ", ".join(map(str, pages))
            text += (f"\n\n🔗 Linked: the same field is also on page {where}. "
                     "It is filled once and shows the same value everywhere. "
                     "Give it another name to unlink it.")  # fmt: skip
        self.info.setText(text)

    @property
    def field(self) -> Field | None:
        return self._field

    def _commit_name(self) -> None:
        f = self._field
        name = self.name.text().strip()
        if f is None or name == f.name:
            return
        if not name or "." in name:
            self.error.setText("Names must not be empty or contain dots.")
        elif self._name_is_taken(name, f):
            self.error.setText(f"“{name}” is already used by a field of another type.")
        else:
            self.error.clear()
            self.changed.emit(f, {"name": name})
            return
        self.name.setText(f.name or "")

    def _commit_type(self) -> None:
        f = self._field
        field_type = FieldType(self.type.currentData())
        if f is not None and field_type is not f.type:
            attrs = {"type": field_type}
            if field_type is not FieldType.TEXT and f.multiline:
                attrs["multiline"] = False
            self.changed.emit(f, attrs)

    def _commit_multiline(self, checked: bool) -> None:
        f = self._field
        if f is not None and checked != f.multiline:
            self.changed.emit(f, {"multiline": checked})
