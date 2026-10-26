"""Page canvas: scanned page + editable field boxes.

Scene units are view points (the core coordinate system), so field boxes map
1:1; only the page pixmap is scaled by 1/px_per_pt.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import ClassVar

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from scan2form.core.layout import ARRANGEMENTS
from scan2form.core.model import BBox, Field, FieldType, Orientation, PageImage
from scan2form.core.refine import binarize, refine_mark
from scan2form.core.straighten import apply_orientation
from scan2form.ui.commands import AddFields, EditGeometry, RemoveFields

TYPE_COLORS = {
    FieldType.TEXT: QColor(30, 110, 220),
    FieldType.CHECKBOX: QColor(20, 150, 60),
    FieldType.SIGNATURE: QColor(150, 60, 180),
}
UNCERTAIN_SCORE = 0.5  # fields at or below are drawn dashed orange: "please check"
UNCERTAIN_COLOR = QColor(235, 140, 0)
HANDLE_PX = 6  # resize handle size on screen
BRUSH_MM = 3.0
Namer = Callable[[Field, int], None]


def to_pixmap(pixels: np.ndarray) -> QPixmap:
    pixels = np.ascontiguousarray(pixels)
    h, w = pixels.shape[:2]
    image = QImage(pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


def _bbox(rect: QRectF) -> BBox:
    r = rect.normalized()
    return BBox(r.left(), r.top(), r.right(), r.bottom())


class Tool(Enum):
    SELECT = "select"
    BRUSH = "brush"
    BOX = "box"


class FieldItem(QGraphicsRectItem):
    _CURSORS: ClassVar[dict[str, Qt.CursorShape]] = {
        "tl": Qt.CursorShape.SizeFDiagCursor,
        "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor,
        "t": Qt.CursorShape.SizeVerCursor,
        "b": Qt.CursorShape.SizeVerCursor,
        "l": Qt.CursorShape.SizeHorCursor,
        "r": Qt.CursorShape.SizeHorCursor,
    }

    def __init__(self, field: Field, pt_per_mm: float) -> None:
        super().__init__()
        self.field = field
        self.pt_per_mm = pt_per_mm
        self._resize_handle: str | None = None
        self._resize_start: QRectF | None = None
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.sync_from_field()

    # -- geometry ---------------------------------------------------------
    def sync_from_field(self) -> None:
        b = self.field.bbox
        self.prepareGeometryChange()
        self.setPos(0, 0)
        self.setRect(QRectF(b.x0, b.y0, b.width, b.height))
        self.setToolTip(f"{self.field.name}  ({self.field.type.value})")
        self.update()

    def live_bbox(self) -> BBox:
        return _bbox(self.mapRectToScene(self.rect()))

    def _view_scale(self) -> float:
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def _label_height(self) -> float:
        return 3.2 * self.pt_per_mm

    def _label_inside(self) -> bool:
        """Names go inside the box, where they are easy to read; boxes too small for a
        line of text (checkboxes) keep the name above."""
        r = self.rect()
        return r.height() >= 2.2 * self.pt_per_mm and r.width() >= 8 * self.pt_per_mm

    def boundingRect(self) -> QRectF:
        margin = HANDLE_PX / self._view_scale()
        above = 0.0 if self._label_inside() else self._label_height()
        return self.rect().adjusted(-margin, -margin - above, margin, margin)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        margin = HANDLE_PX / self._view_scale()
        path.addRect(self.rect().adjusted(-margin, -margin, margin, margin))
        return path

    def _handle_points(self) -> dict[str, QPointF]:
        r = self.rect()
        corners = {
            "tl": r.topLeft(),
            "tr": r.topRight(),
            "bl": r.bottomLeft(),
            "br": r.bottomRight(),
        }
        # Small boxes (checkboxes) get corner handles only, so the middle stays grabbable for moving.
        if min(r.width(), r.height()) * self._view_scale() < 5 * HANDLE_PX:
            return corners
        return corners | {
            "t": QPointF(r.center().x(), r.top()), "b": QPointF(r.center().x(), r.bottom()),
            "l": QPointF(r.left(), r.center().y()), "r": QPointF(r.right(), r.center().y()),
        }  # fmt: skip

    def _handle_at(self, pos: QPointF) -> str | None:
        if not self.isSelected():
            return None
        tolerance = HANDLE_PX / self._view_scale()
        for name, point in self._handle_points().items():
            if abs(point.x() - pos.x()) <= tolerance and abs(point.y() - pos.y()) <= tolerance:
                return name
        return None

    # -- painting ---------------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None) -> None:
        color = TYPE_COLORS[self.field.type]
        r = self.rect()
        fill = QColor(color)
        fill.setAlpha(70 if self.isSelected() else 40)
        painter.fillRect(r, fill)
        uncertain = self.field.score <= UNCERTAIN_SCORE
        pen = QPen(UNCERTAIN_COLOR if uncertain else color, 2.5 if self.isSelected() else 1.5)
        pen.setCosmetic(True)
        if uncertain:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(r)

        name = self.field.name or ""
        font = QFont()
        if self._label_inside():
            pad = 0.8 * self.pt_per_mm
            size = min(4.5 * self.pt_per_mm, r.height() * 0.6)
            font.setPixelSize(max(1, int(size)))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QPen((UNCERTAIN_COLOR if uncertain else color).darker(140)))
            text_rect = QRectF(r.left() + pad, r.top(), r.width() - 2 * pad, r.height())
            if not self.field.multiline:
                align = Qt.AlignmentFlag.AlignVCenter
            else:  # tall boxes: name in the top-left corner, like a form caption
                align = Qt.AlignmentFlag.AlignTop
                text_rect.setTop(r.top() + pad / 2)
            elided = painter.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, int(text_rect.width())
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | align, elided)
        else:
            font.setPixelSize(max(1, int(self._label_height() * 0.8)))
            painter.setFont(font)
            painter.drawText(
                QRectF(
                    r.left(),
                    r.top() - self._label_height(),
                    max(r.width(), 300),
                    self._label_height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                name,
            )
        if self.isSelected():
            size = HANDLE_PX / self._view_scale()
            painter.setBrush(QBrush(QColor("white")))
            for point in self._handle_points().values():
                painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))

    # -- interaction --------------------------------------------------------
    def hoverMoveEvent(self, event) -> None:
        handle = self._handle_at(event.pos())
        self.setCursor(self._CURSORS[handle] if handle else Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        handle = self._handle_at(event.pos())
        if handle and event.button() == Qt.MouseButton.LeftButton:
            self._resize_handle, self._resize_start = handle, QRectF(self.rect())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_handle:
            r, p = QRectF(self._resize_start), event.pos()
            if "l" in self._resize_handle:
                r.setLeft(p.x())
            if "r" in self._resize_handle:
                r.setRight(p.x())
            if "t" in self._resize_handle:
                r.setTop(p.y())
            if "b" in self._resize_handle:
                r.setBottom(p.y())
            r = r.normalized()
            if r.width() >= 2 and r.height() >= 2:
                self.prepareGeometryChange()
                self.setRect(r)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._resize_handle = None
        super().mouseReleaseEvent(event)
        if isinstance(self.scene(), PageScene):
            self.scene().commit_geometry()


class PageScene(QGraphicsScene):
    message = Signal(str)
    reoriented = Signal(int)  # page index; the page image and all field boxes changed

    def __init__(self, page: PageImage, undo_stack: QUndoStack, namer: Namer) -> None:
        super().__init__()
        self.page, self.undo_stack, self.namer = page, undo_stack, namer
        self._items: dict[int, FieldItem] = {}
        self._binary: np.ndarray | None = None
        self.background = QGraphicsPixmapItem()
        self.background.setScale(1.0 / page.px_per_pt)
        self.background.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.background.setZValue(0)
        self.addItem(self.background)
        self._show_page_image()
        self.add_items(page.fields, select=False)

    def _show_page_image(self) -> None:
        self.background.setPixmap(to_pixmap(self.page.pixels))
        self.setSceneRect(0, 0, self.page.width_pt, self.page.height_pt)

    def set_orientation(self, orientation: Orientation) -> None:
        """Re-render the page in a new orientation; fields move with the content. Use via ReorientPage."""
        apply_orientation(self.page, orientation)
        self._binary = None
        self._show_page_image()
        for item in self._items.values():
            item.sync_from_field()
        self.reoriented.emit(self.page.index)

    @property
    def binary(self) -> np.ndarray:
        if self._binary is None:
            self._binary = binarize(self.page)
        return self._binary

    def item_for(self, field: Field) -> FieldItem | None:
        return self._items.get(id(field))

    def add_items(self, fields: list[Field], select: bool) -> None:
        if select:
            self.clearSelection()
        for f in fields:
            item = FieldItem(f, self.page.pt_per_mm)
            self._items[id(f)] = item
            self.addItem(item)
            item.setSelected(select)

    def remove_items(self, fields: list[Field]) -> None:
        for f in fields:
            item = self._items.pop(id(f), None)
            if item is not None:
                self.removeItem(item)

    def refresh(self, field: Field) -> None:
        item = self.item_for(field)
        if item is not None:
            item.sync_from_field()

    def selected_fields(self) -> list[Field]:
        return [i.field for i in self.selectedItems() if isinstance(i, FieldItem)]

    def commit_geometry(self) -> None:
        """Turn live item geometry changes (drag, resize, nudge) into one undo step."""
        changes = []
        for item in self._items.values():
            live = item.live_bbox()
            old = item.field.bbox
            if max(abs(a - b) for a, b in zip(
                (live.x0, live.y0, live.x1, live.y1), (old.x0, old.y0, old.x1, old.y1)
            )) > 0.01:  # fmt: skip
                changes.append((item.field, old, live))
        if changes:
            self.undo_stack.push(EditGeometry(self, changes))

    def add_from_mark(self, mark: BBox, exact: bool) -> Field:
        """Create a field from a brush mark (refined) or a drawn box (exact)."""
        if exact:
            tall = mark.height >= 12 * self.page.pt_per_mm
            field = Field(FieldType.TEXT, mark, multiline=tall)
        else:
            field = refine_mark(self.page, mark, self.binary)
            if field.score < 1.0:
                self.message.emit("Nothing printed to snap to here; kept the painted area.")
        self.namer(field, self.page.index)
        self.undo_stack.push(AddFields(self, [field]))
        return field

    def delete_selected(self) -> None:
        fields = self.selected_fields()
        if fields:
            self.undo_stack.push(RemoveFields(self, fields))

    def arrange(self, key: str) -> bool:
        """Apply a core.layout arrangement to the selection as one undo step."""
        fields = self.selected_fields()
        if len(fields) < 2:
            return False
        label, arrange = ARRANGEMENTS[key]
        new_boxes = arrange([f.bbox for f in fields])
        changes = [(f, f.bbox, new) for f, new in zip(fields, new_boxes) if new != f.bbox]
        if changes:
            command = EditGeometry(self, changes)
            command.setText(f"{label} ({len(fields)} fields)")
            self.undo_stack.push(command)
        return True

    def nudge(self, dx: float, dy: float) -> None:
        for item in self.selectedItems():
            if isinstance(item, FieldItem):
                item.moveBy(dx, dy)
        self.commit_geometry()


class PageView(QGraphicsView):
    zoomChanged = Signal(float)
    MIN_ZOOM, MAX_ZOOM = 0.2, 12.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setBackgroundBrush(QColor(90, 90, 96))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.tool = Tool.SELECT
        self._stroke: list[QPointF] = []
        self._overlay: QGraphicsPathItem | QGraphicsRectItem | None = None
        self._box_start: QPointF | None = None
        self.show_grid = False
        self.set_tool(Tool.SELECT)

    def set_grid(self, visible: bool) -> None:
        self.show_grid = visible
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """Alignment grid (10 mm, stronger every 50 mm) to judge straightening against."""
        scene = self.page_scene()
        if not self.show_grid or scene is None:
            return
        mm = scene.page.pt_per_mm
        page_rect = scene.sceneRect()
        for step, alpha in ((10 * mm, 60), (50 * mm, 140)):
            pen = QPen(QColor(0, 150, 220, alpha), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            x = 0.0
            while x <= page_rect.width():
                painter.drawLine(QPointF(x, 0), QPointF(x, page_rect.height()))
                x += step
            y = 0.0
            while y <= page_rect.height():
                painter.drawLine(QPointF(0, y), QPointF(page_rect.width(), y))
                y += step

    def page_scene(self) -> PageScene | None:
        scene = self.scene()
        return scene if isinstance(scene, PageScene) else None

    # -- tools ------------------------------------------------------------
    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        if tool is Tool.SELECT:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            # Only fields fully inside the drawn box get selected, not their neighbours.
            self.setRubberBandSelectionMode(Qt.ItemSelectionMode.ContainsItemShape)
            self.viewport().unsetCursor()
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event) -> None:
        scene = self.page_scene()
        if scene is None or self.tool is Tool.SELECT or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = self.mapToScene(event.position().toPoint())
        if self.tool is Tool.BRUSH:
            self._stroke = [point]
            color = QColor(230, 30, 30, 110)
            pen = QPen(color, BRUSH_MM * scene.page.pt_per_mm, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)  # fmt: skip
            self._overlay = scene.addPath(QPainterPath(point), pen)
        else:
            self._box_start = point
            pen = QPen(TYPE_COLORS[FieldType.TEXT], 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._overlay = scene.addRect(QRectF(point, point), pen)
        self._overlay.setZValue(20)

    def mouseMoveEvent(self, event) -> None:
        if self._overlay is None:
            super().mouseMoveEvent(event)
            return
        point = self.mapToScene(event.position().toPoint())
        if self.tool is Tool.BRUSH:
            self._stroke.append(point)
            path = self._overlay.path()
            path.lineTo(point)
            self._overlay.setPath(path)
        else:
            self._overlay.setRect(QRectF(self._box_start, point).normalized())

    def mouseReleaseEvent(self, event) -> None:
        scene = self.page_scene()
        if self._overlay is None or scene is None:
            super().mouseReleaseEvent(event)
            return
        scene.removeItem(self._overlay)
        self._overlay = None
        if self.tool is Tool.BRUSH:
            self.finish_stroke(self._stroke)
        else:
            rect = QRectF(self._box_start, self.mapToScene(event.position().toPoint())).normalized()
            if (
                rect.width() >= 2 * scene.page.pt_per_mm
                and rect.height() >= 2 * scene.page.pt_per_mm
            ):
                scene.add_from_mark(_bbox(rect), exact=True)
        self._stroke, self._box_start = [], None

    def finish_stroke(self, points: list[QPointF]) -> Field | None:
        """Brush stroke (scene points) -> refined field. Public for tests and future input methods."""
        scene = self.page_scene()
        if scene is None or not points:
            return None
        mm = scene.page.pt_per_mm
        xs, ys = [p.x() for p in points], [p.y() for p in points]
        mark = BBox(min(xs), min(ys), max(xs), max(ys))
        if mark.width < 2 * mm and mark.height < 2 * mm:  # a click or dab: look for a checkbox here
            cx, cy = (mark.x0 + mark.x1) / 2, (mark.y0 + mark.y1) / 2
            mark = BBox(cx - 2.5 * mm, cy - 2.5 * mm, cx + 2.5 * mm, cy + 2.5 * mm)
        else:  # the centre line of the stroke, padded a little
            pad = 0.75 * mm
            mark = BBox(mark.x0 - pad, mark.y0 - pad, mark.x1 + pad, mark.y1 + pad)
        return scene.add_from_mark(mark, exact=False)

    # -- keyboard & zoom ------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        scene = self.page_scene()
        if scene is not None and scene.selected_fields():
            step = 5.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            moves = {
                Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step),
            }  # fmt: skip
            if event.key() in moves:
                scene.nudge(*moves[event.key()])
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                scene.delete_selected()
                return
        super().keyPressEvent(event)

    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, value: float) -> None:
        value = min(self.MAX_ZOOM, max(self.MIN_ZOOM, value))
        self.resetTransform()
        self.scale(value, value)
        self.zoomChanged.emit(value)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom() * (1.15 ** (event.angleDelta().y() / 120)))
            return
        super().wheelEvent(event)

    def fit_width(self) -> None:
        scene = self.page_scene()
        if scene is not None:
            margin = 24
            self.set_zoom((self.viewport().width() - margin) / scene.sceneRect().width())
