"""Classical field detection with OpenCV (permissively licensed default backend).

Finds three visual patterns on blank forms:
  * small near-square boxes            -> checkboxes
  * larger, mostly empty rectangles    -> text fields (multiline when tall)
  * long horizontal rules/underscores  -> text fields sitting on the line

Not covered (by design, see spike results): dotted leaders, signature
classification (needs OCR labels), fields without any visual cue.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan2form.core.detect.base import non_max_suppression
from scan2form.core.model import BBox, Field, FieldType, PageImage


@dataclass
class OpenCVDetectorConfig:
    checkbox_min_mm: float = 2.5
    checkbox_max_mm: float = 8.0
    checkbox_max_aspect: float = 1.35
    box_min_width_mm: float = 12.0
    box_min_height_mm: float = 4.0
    box_max_height_mm: float = 60.0
    multiline_min_height_mm: float = 12.0
    line_min_length_mm: float = 15.0
    line_max_thickness_mm: float = 1.2
    line_field_height_mm: float = 6.0
    max_ink_ratio: float = 0.06


class OpenCVDetector:
    name = "opencv"

    def __init__(self, config: OpenCVDetectorConfig | None = None) -> None:
        self.config = config or OpenCVDetectorConfig()

    def detect(self, page: PageImage) -> list[Field]:
        cfg = self.config
        px_per_mm = page.px_per_mm
        gray = cv2.cvtColor(page.pixels, cv2.COLOR_RGB2GRAY)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15
        )
        page_h, page_w = binary.shape

        boxes_px = self._find_rectangles(binary, px_per_mm, page_w, page_h)
        lines_px = self._find_horizontal_lines(binary, px_per_mm)

        fields: list[Field] = []
        for x, y, w, h in boxes_px:
            fields.append(self._classify_box(binary, x, y, w, h, px_per_mm))
        for x, y, w, h in lines_px:
            if any(_is_box_edge(x, y, w, h, b, tol=1.5 * px_per_mm) for b in boxes_px):
                continue
            field_h = int(cfg.line_field_height_mm * px_per_mm)
            top = max(0, y - field_h)
            if _ink_ratio(binary, x, top, w, y - top - 1) > cfg.max_ink_ratio:
                continue  # text directly above: a table rule or underlined heading
            fields.append(Field(FieldType.TEXT, BBox(x, top, x + w, y + h), score=0.6))

        fields = [f for f in fields if f is not None]
        scale = 1.0 / page.px_per_pt
        return non_max_suppression(
            [Field(f.type, f.bbox.scaled(scale), f.score, f.multiline) for f in fields]
        )

    def _find_rectangles(self, binary: np.ndarray, px_per_mm: float, page_w: int, page_h: int):
        cfg = self.config
        # Close small gaps in scanned box borders before tracing contours.
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        min_cb, max_cb = cfg.checkbox_min_mm * px_per_mm, cfg.checkbox_max_mm * px_per_mm
        rects = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            is_checkbox_size = min_cb <= w <= max_cb and min_cb <= h <= max_cb
            is_box_size = (
                w >= cfg.box_min_width_mm * px_per_mm
                and cfg.box_min_height_mm * px_per_mm <= h <= cfg.box_max_height_mm * px_per_mm
            )
            if not (is_checkbox_size or is_box_size):
                continue
            if w > 0.9 * page_w and h > 0.5 * page_h:
                continue  # page frame
            # Cap epsilon by the short side, or thin wide boxes collapse to a line.
            epsilon = min(0.02 * cv2.arcLength(contour, True), 0.2 * min(w, h))
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            if cv2.contourArea(approx) < 0.85 * w * h:
                continue
            rects.append((x, y, w, h))
        return _dedupe_rects(rects)

    def _classify_box(self, binary, x, y, w, h, px_per_mm) -> Field | None:
        cfg = self.config
        aspect = max(w, h) / max(1, min(w, h))
        max_cb = cfg.checkbox_max_mm * px_per_mm
        bbox = BBox(x, y, x + w, y + h)
        if w <= max_cb and h <= max_cb:
            if aspect > cfg.checkbox_max_aspect:
                return None
            return Field(FieldType.CHECKBOX, bbox, score=0.8)
        # Text boxes must be mostly empty inside (otherwise it is a label cell).
        inset = max(2, int(0.8 * px_per_mm))
        if (
            _ink_ratio(binary, x + inset, y + inset, w - 2 * inset, h - 2 * inset)
            > cfg.max_ink_ratio
        ):
            return None
        multiline = h >= cfg.multiline_min_height_mm * px_per_mm
        return Field(FieldType.TEXT, bbox, score=0.7, multiline=multiline)

    def _find_horizontal_lines(self, binary: np.ndarray, px_per_mm: float):
        cfg = self.config
        kernel_len = max(10, int(cfg.line_min_length_mm * px_per_mm))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        max_thickness = cfg.line_max_thickness_mm * px_per_mm
        return [
            (x, y, w, h)
            for x, y, w, h in map(cv2.boundingRect, contours)
            if h <= max_thickness and w >= kernel_len
        ]


def _ink_ratio(binary: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    if w <= 0 or h <= 0:
        return 0.0
    region = binary[y : y + h, x : x + w]
    return float(np.count_nonzero(region)) / region.size if region.size else 0.0


def _is_box_edge(x, y, w, h, box, tol: float) -> bool:
    bx, by, bw, bh = box
    horizontal_overlap = min(x + w, bx + bw) - max(x, bx) > 0.5 * w
    on_edge = abs(y - by) <= tol or abs((y + h) - (by + bh)) <= tol
    return horizontal_overlap and on_edge


def _dedupe_rects(rects):
    """Drop near-duplicate contours (inner and outer edge of the same stroke)."""
    result = []
    for rect in sorted(rects, key=lambda r: r[2] * r[3], reverse=True):
        x, y, w, h = rect
        duplicate = any(
            abs(x - rx) <= 6 and abs(y - ry) <= 6 and abs(w - rw) <= 12 and abs(h - rh) <= 12
            for rx, ry, rw, rh in result
        )
        if not duplicate:
            result.append(rect)
    return result
