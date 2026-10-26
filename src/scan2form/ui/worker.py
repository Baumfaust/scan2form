"""Run slow core work (detection, OCR, export) off the UI thread."""

from __future__ import annotations

import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class JobSignals(QObject):
    progress = Signal(int, int, str)  # page index, page count, step
    finished = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """`fn(progress)` runs in a pool thread; `progress(page_index, page_count, step)` reports back."""

    def __init__(self, fn: Callable) -> None:
        super().__init__()
        self.fn = fn
        self.signals = JobSignals()

    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, never swallowed
            traceback.print_exc()
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(result)
