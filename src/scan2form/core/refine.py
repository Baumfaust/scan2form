"""Turn a rough user mark into a precise field ("assisted marking").

The user paints roughly over a spot (in the review UI, or with a red pen on a
screenshot); the page is inspected again around that spot:
  * small compact mark                -> checkbox, snapped to the printed square
  * mark over a printed empty box     -> text field filling that box
  * thin horizontal stroke            -> text field on the printed line below it,
                                         extended to the line's full length
  * anything else                     -> text field covering the painted area
"""

from __future__ import annotations

import cv2
import numpy as np

from scan2form.core.model import BBox, Field, FieldType, PageImage

CHECKBOX_MIN_MM = 2.5
CHECKBOX_MAX_MM = 8.0
LINE_FIELD_HEIGHT_MM = 6.0
STROKE_MAX_HEIGHT_MM = 4.0
MULTILINE_MIN_HEIGHT_MM = 12.0
MAX_BOX_MM = 100.0  # tallest text box we snap to; also bounds the search area


def binarize(page: PageImage) -> np.ndarray:
    gray = cv2.cvtColor(page.pixels, cv2.COLOR_RGB2GRAY)
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )


def refine_mark(page: PageImage, mark: BBox, binary: np.ndarray | None = None) -> Field:
    """Refine a rough mark given in page points. Pass `binary` to reuse it across calls."""
    if binary is None:
        binary = binarize(page)
    s, px_per_mm = page.px_per_pt, page.px_per_mm
    x0, y0, x1, y1 = mark.x0 * s, mark.y0 * s, mark.x1 * s, mark.y1 * s
    w, h = x1 - x0, y1 - y0
    to_pt = 1.0 / s

    def field(kind: FieldType, box, multiline: bool = False, score: float = 1.0) -> Field:
        return Field(kind, BBox(*box).scaled(to_pt), score=score, multiline=multiline)

    horizontal_stroke = w >= 2.5 * h
    small = w <= CHECKBOX_MAX_MM * px_per_mm and h <= CHECKBOX_MAX_MM * px_per_mm
    if small and not horizontal_stroke:
        box = snap_checkbox(binary, x0, y0, x1, y1, px_per_mm)
        return field(FieldType.CHECKBOX, box or (x0, y0, x1, y1), score=1.0 if box else 0.5)

    box = snap_enclosing_box(binary, x0, y0, x1, y1, px_per_mm)
    if box is not None:
        multiline = box[3] - box[1] >= MULTILINE_MIN_HEIGHT_MM * px_per_mm
        return field(FieldType.TEXT, box, multiline)

    if h < STROKE_MAX_HEIGHT_MM * px_per_mm:
        line = snap_line(binary, x0, x1, (y0 + y1) / 2, px_per_mm)
        if line is not None:
            lx0, lx1, line_y = line
        else:
            lx0, lx1, line_y = x0, x1, y1
        top = line_y - LINE_FIELD_HEIGHT_MM * px_per_mm
        return field(FieldType.TEXT, (lx0, top, lx1, line_y), score=1.0 if line else 0.5)

    multiline = h >= MULTILINE_MIN_HEIGHT_MM * px_per_mm
    return field(FieldType.TEXT, (x0, y0, x1, y1), multiline, score=0.5)


def _region(binary: np.ndarray, x0, y0, x1, y1, margin: float):
    h, w = binary.shape
    rx0, ry0 = max(0, int(x0 - margin)), max(0, int(y0 - margin))
    rx1, ry1 = min(w, int(x1 + margin)), min(h, int(y1 + margin))
    return rx0, ry0, binary[ry0:ry1, rx0:rx1]


def snap_checkbox(binary, x0, y0, x1, y1, px_per_mm):
    """Printed square nearest to the mark centre, or None."""
    rx0, ry0, roi = _region(binary, x0, y0, x1, y1, 3 * px_per_mm)
    contours, _ = cv2.findContours(roi, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    lo, hi = CHECKBOX_MIN_MM * px_per_mm, CHECKBOX_MAX_MM * px_per_mm
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    best = None
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        if not (lo <= bw <= hi and lo <= bh <= hi) or max(bw, bh) > 1.4 * min(bw, bh):
            continue
        dist = abs(rx0 + bx + bw / 2 - cx) + abs(ry0 + by + bh / 2 - cy)
        if best is None or dist < best[0]:
            best = (dist, (rx0 + bx, ry0 + by, rx0 + bx + bw, ry0 + by + bh))
    return best[1] if best else None


def snap_enclosing_box(binary, x0, y0, x1, y1, px_per_mm):
    """Smallest printed rectangle containing the mark centre, or None.

    "Smallest" makes a dab into one table cell select that cell, not the table.
    Rectangles must contain most of the mark and stay below form-box size limits.
    """
    margin = MAX_BOX_MM * px_per_mm
    rx0, ry0, roi = _region(binary, x0, y0, x1, y1, margin)
    closed = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    best = None
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        bx, by = bx + rx0, by + ry0
        if not (bx < cx < bx + bw and by < cy < by + bh):
            continue
        if bh < 4 * px_per_mm or bh > MAX_BOX_MM * px_per_mm or bw > 2 * MAX_BOX_MM * px_per_mm:
            continue
        if bw < 0.5 * (x1 - x0):
            continue
        epsilon = min(0.02 * cv2.arcLength(contour, True), 0.2 * min(bw, bh))
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) != 4 or cv2.contourArea(approx) < 0.85 * bw * bh:
            continue
        if best is None or bw * bh < best[2] * best[3]:
            best = (bx, by, bw, bh)
    if best is None:
        return None
    bx, by, bw, bh = best
    inset = max(2, int(0.5 * px_per_mm))
    return (bx + inset, by + inset, bx + bw - inset, by + bh - inset)


def snap_line(binary, x0, x1, y_center, px_per_mm):
    """Printed horizontal line under a stroke as (x0, x1, y), or None.

    Searches +-2.5 mm vertically; then follows the line left and right beyond
    the stroke (gaps up to 1 mm), stopping at most 80 mm away.
    """
    reach = int(2.5 * px_per_mm)
    height, width = binary.shape
    top, bottom = max(0, int(y_center) - reach), min(height, int(y_center) + reach)
    ix0, ix1 = max(0, int(x0)), min(width, int(x1))
    band = binary[top:bottom, ix0:ix1]
    if band.size == 0:
        return None
    coverage = band.mean(axis=1) / 255
    row = int(np.argmax(coverage))
    if coverage[row] < 0.5:
        return None
    y = top + row
    # Use a 3-row strip so slightly wavy scanned lines are still followed.
    strip = binary[max(0, y - 1) : y + 2].max(axis=0) > 0
    max_gap, limit = int(1.0 * px_per_mm), int(80 * px_per_mm)

    def follow(start: int, step: int) -> int:
        pos, gap, last_ink = start, 0, start
        while 0 <= pos + step < width and abs(pos - start) < limit:
            pos += step
            if strip[pos]:
                last_ink, gap = pos, 0
            else:
                gap += 1
                if gap > max_gap:
                    break
        return last_ink

    return float(follow(ix0, -1)), float(follow(ix1 - 1, 1)), float(y)
