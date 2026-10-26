"""Fill-out canvas: the page with real input boxes on top of its fields.

Separate from PageScene so nothing typed here can move, resize or delete a field.
Text boxes use the same font size the filled PDF will get (core.pdfform), so what
you see is what the copy looks like.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QLineEdit,
    QPlainTextEdit,
)

from scan2form.core.autofill.session import FillSession
from scan2form.core.model import PT_PER_MM, Field, FieldType, PageImage
from scan2form.core.pdfform import fit_font_size, text_font_size
from scan2form.ui.page_view import to_pixmap

FILL_COLOR = QColor(20, 150, 60)
_EDIT_STYLE = (
    "background: rgba(20, 150, 60, 28); border: none; "
    "border-bottom: 1px solid rgba(20, 150, 60, 160); padding: 0px; color: black;"
)


class CheckItem(QGraphicsRectItem):
    """Checkbox or radio button: click or Space toggles; radios untick their group."""

    def __init__(self, scene: FillScene, field: Field) -> None:
        b = field.bbox
        super().__init__(QRectF(b.x0, b.y0, b.width, b.height))
        self.fill_scene, self.field = scene, field
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)

    @property
    def checked(self) -> bool:
        value = self.fill_scene.session.value(self.field.name)
        return value == self.field.choice if self.field.choice is not None else bool(value)

    def toggle(self) -> None:
        if self.field.choice is not None:
            new = None if self.checked else self.field.choice
        else:
            new = not self.checked
        self.fill_scene.edit(self.field.name, new)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.toggle()
            event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_X):
            self.toggle()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        self.fill_scene.focused.emit(self.field.name)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self.rect()
        fill = QColor(FILL_COLOR)
        fill.setAlpha(60 if self.hasFocus() else 28)
        painter.fillRect(r, fill)
        pen = QPen(FILL_COLOR, 2.0 if self.hasFocus() else 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(r)
        if self.checked:
            w, h = r.width(), r.height()
            pen = QPen(QColor("black"), max(0.8, min(w, h) / 10))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPolyline([r.topLeft() + QPointF(fx * w, fy * h)
                                  for fx, fy in ((0.2, 0.5), (0.42, 0.75), (0.8, 0.22))])  # fmt: skip


class FillScene(QGraphicsScene):
    edited = Signal(str)  # field id whose value the user changed on the page
    focused = Signal(str)  # field id that got the keyboard focus
    committed = Signal(str)  # field id the user finished typing into (left the box)

    def __init__(self, page: PageImage, session: FillSession) -> None:
        super().__init__()
        self.page, self.session = page, session
        self.background = QGraphicsPixmapItem(to_pixmap(page.pixels))
        self.background.setScale(1.0 / page.px_per_pt)
        self.background.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.addItem(self.background)
        self.setSceneRect(0, 0, page.width_pt, page.height_pt)
        self._scale = page.pt_per_mm / PT_PER_MM
        self.editors: dict[str, list] = {}  # field id -> widgets/items showing it
        self._updating = False
        ordered = sorted(
            page.fields, key=lambda f: (round(f.bbox.y0 / (3 * page.pt_per_mm)), f.bbox.x0)
        )
        for f in ordered:
            self._add_editor(f)

    # -- building ------------------------------------------------------------------
    def _add_editor(self, field: Field) -> None:
        b = field.bbox
        form_field = self.session.by_id.get(field.name or "")
        if field.type is FieldType.SIGNATURE or form_field is None:
            hint = self.addRect(
                QRectF(b.x0, b.y0, b.width, b.height), QPen(QColor(150, 60, 180, 120), 0)
            )
            hint.setBrush(QBrush(QColor(150, 60, 180, 18)))
            hint.setToolTip("Signature: sign the printed copy")
            return
        if field.type is FieldType.CHECKBOX:
            item = CheckItem(self, field)
            self.addItem(item)
            self.editors.setdefault(field.name, []).append(item)
            return
        size = text_font_size(b.height, self._scale)
        if form_field.kind == "choice" and form_field.choices:
            widget = QComboBox()
            widget.addItem("", "")
            for choice in form_field.choices:
                widget.addItem(choice, choice)
            widget.currentIndexChanged.connect(lambda _i, w=widget, f=field: self._changed(f, w))
            widget.activated.connect(lambda _i, f=field: self.committed.emit(f.name))
        elif field.multiline:
            widget = QPlainTextEdit()
            widget.setFrameShape(QFrame.Shape.NoFrame)
            widget.textChanged.connect(lambda w=widget, f=field: self._changed(f, w))
        else:
            widget = QLineEdit()
            widget.setFrame(False)
            widget.textEdited.connect(lambda _t, w=widget, f=field: self._changed(f, w))
            widget.returnPressed.connect(self._focus_next)
            widget.editingFinished.connect(lambda f=field: self.committed.emit(f.name))
        font = QFont("Helvetica")
        font.setPixelSize(max(1, round(size)))
        widget.setFont(font)
        widget.setStyleSheet(_EDIT_STYLE)
        widget.setContentsMargins(0, 0, 0, 0)
        widget.setMinimumSize(1, 1)
        widget.setFixedSize(max(1, round(b.width)), max(1, round(b.height)))
        widget.setToolTip(form_field.label if form_field.label != form_field.name
                          else form_field.name)  # fmt: skip
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(widget)
        proxy.setPos(b.x0, b.y0)
        proxy.setZValue(10)
        self.addItem(proxy)
        widget.installEventFilter(self)
        widget.setProperty("field_id", field.name)
        self.editors.setdefault(field.name, []).append(widget)
        self._show(field, widget)

    # -- values ------------------------------------------------------------------------
    def _text_of(self, widget) -> str:
        if isinstance(widget, QComboBox):
            return widget.currentData() or ""
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        return widget.text()

    def _fit(self, field: Field, widget) -> None:
        if isinstance(widget, QLineEdit):
            b = field.bbox
            size = fit_font_size(
                widget.text(), text_font_size(b.height, self._scale), b.width, self._scale
            )
            font = widget.font()
            if font.pixelSize() != max(1, round(size)):
                font.setPixelSize(max(1, round(size)))
                widget.setFont(font)

    def _show(self, field: Field, widget) -> None:
        value = self.session.value(field.name)
        text = value if isinstance(value, str) else ""
        self._updating = True
        try:
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(text)))
            elif isinstance(widget, QPlainTextEdit):
                if widget.toPlainText() != text:
                    widget.setPlainText(text)
            elif widget.text() != text:
                widget.setText(text)
                widget.setCursorPosition(0)
            self._fit(field, widget)
        finally:
            self._updating = False

    def _changed(self, field: Field, widget) -> None:
        if self._updating:
            return
        self._fit(field, widget)
        self.edit(field.name, self._text_of(widget), source=widget)

    def edit(self, field_id: str, value, source=None) -> None:
        """A value typed or ticked on the page."""
        self.session.set_value(field_id, value if value not in ("",) else None)
        self.refresh(field_id, skip=source)
        self.edited.emit(field_id)

    def refresh(self, field_id: str | None = None, skip=None) -> None:
        """Show the session's current value(s) (after the panel or 'Fill from profile')."""
        ids = [field_id] if field_id else list(self.editors)
        fields = {f.name: f for f in self.page.fields}
        for fid in ids:
            for editor in self.editors.get(fid, []):
                if editor is skip:
                    continue
                if isinstance(editor, CheckItem):
                    for item in self.editors[fid]:
                        item.update()
                    break
                self._show(fields[fid], editor)

    # -- keyboard ----------------------------------------------------------------------
    def _focus_next(self) -> None:
        self.focusNextPrevChild(True)

    def focus_field(self, field_id: str) -> QGraphicsItem | None:
        """Give `field_id` the keyboard focus; returns its item so the view can scroll to it."""
        for editor in self.editors.get(field_id, []):
            if isinstance(editor, CheckItem):
                editor.setFocus()
                return editor
            editor.setFocus()
            return editor.graphicsProxyWidget()
        return None

    def eventFilter(self, obj, event) -> bool:
        if event.type() == event.Type.FocusIn and obj.property("field_id"):
            self.focused.emit(obj.property("field_id"))
        elif (event.type() == event.Type.FocusOut and isinstance(obj, QPlainTextEdit)
              and obj.property("field_id")):  # fmt: skip
            self.committed.emit(obj.property("field_id"))
        return super().eventFilter(obj, event)
