"""Image pre-processing: skew estimation and correction."""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from scan2form.core.model import BBox, PageImage

MAX_SKEW_DEG = 10.0
MIN_CORRECTION_DEG = 0.15


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate page skew in degrees (positive = content rotated counter-clockwise).

    Uses the median angle of long, nearly horizontal Hough lines, which on forms
    come from rules, underlines and text baselines.
    """
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    edges = cv2.Canny(binary, 50, 150)
    min_len = max(50, gray.shape[1] // 8)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 1800, threshold=100, minLineLength=min_len, maxLineGap=10
    )
    if lines is None:
        return 0.0
    angles = []
    for x0, y0, x1, y1 in lines.reshape(-1, 4):  # shape differs between OpenCV 4 and 5
        angle = np.degrees(np.arctan2(y0 - y1, x1 - x0))
        if abs(angle) <= MAX_SKEW_DEG:
            angles.append(angle)
    return float(np.median(angles)) if angles else 0.0


def _rotation(page: PageImage, angle: float) -> np.ndarray:
    h, w = page.pixels.shape[:2]
    return cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)


def unskew_bbox(bbox: BBox, deskewed: PageImage) -> BBox:
    """Map a box from deskewed-page points back to the original page.

    The centre is rotated back exactly; width and height are kept, which is
    accurate for the small angles deskew corrects (<= MAX_SKEW_DEG).
    """
    if not deskewed.skew_deg:
        return bbox
    inverse = cv2.invertAffineTransform(_rotation(deskewed, deskewed.skew_deg))
    s = deskewed.px_per_pt
    cx, cy = (bbox.x0 + bbox.x1) / 2 * s, (bbox.y0 + bbox.y1) / 2 * s
    ox, oy = inverse @ np.array([cx, cy, 1.0])
    hw, hh = bbox.width / 2, bbox.height / 2
    return BBox(ox / s - hw, oy / s - hh, ox / s + hw, oy / s + hh)


def deskew(page: PageImage) -> PageImage:
    """Return the page rotated upright. Pages with negligible skew are returned unchanged."""
    gray = cv2.cvtColor(page.pixels, cv2.COLOR_RGB2GRAY)
    angle = estimate_skew(gray)
    if abs(angle) < MIN_CORRECTION_DEG:
        return page
    h, w = gray.shape
    matrix = _rotation(page, angle)
    rotated = cv2.warpAffine(
        page.pixels, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255)
    )
    return replace(page, pixels=rotated, skew_deg=angle)
