"""Large, centred progress card over the page area while background work runs."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget

from scan2form.core.pipeline import STEPS

STEP_LABELS = {"deskew": "straightening page", "detect": "finding fields", "ocr": "reading labels"}


class ProgressOverlay(QWidget):
    """Covers its parent completely; follows the parent's size via an event filter."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.card = QFrame(self)
        self.card.setObjectName("progressCard")
        self.card.setStyleSheet(
            "#progressCard { background: palette(window); border-radius: 10px; border: 1px solid #9aa3b5; }"
        )
        self.card.setMinimumWidth(460)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.title.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setMinimumHeight(24)
        self.bar.setTextVisible(True)
        self.step = QLabel()
        self.step.setStyleSheet("color: palette(placeholder-text); font-size: 13px;")
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addWidget(self.step)
        parent.installEventFilter(self)
        self.hide()

    # -- public API ---------------------------------------------------------
    def start(self, title: str) -> None:
        self.title.setText(title)
        self.step.setText("Starting…")
        self.bar.setRange(0, 0)  # indeterminate until the first progress report
        self._fit()
        self.show()
        self.raise_()

    def report(self, page_index: int, page_count: int, step: str) -> None:
        steps = len(STEPS)
        position = STEPS.index(step) if step in STEPS else 0
        self.bar.setRange(0, max(1, page_count * steps))
        self.bar.setValue(page_index * steps + position)
        self.step.setText(f"Page {page_index + 1} of {page_count} · {STEP_LABELS.get(step, step)}")

    def finish(self) -> None:
        self.hide()

    # -- layout & painting ------------------------------------------------------
    def _fit(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.card.adjustSize()
        self.card.move(
            (self.width() - self.card.width()) // 2, (self.height() - self.card.height()) // 2
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 24, 34, 120))  # dim the page behind the card
