"""Page orientation: quarter turns + fine straightening angle.

A page has an unrotated source render ("base") and a view = base turned by
`quarter_turns` x 90 deg clockwise, then rotated by `straighten_deg` around its
centre (OpenCV convention: positive = counter-clockwise on screen) on a canvas
of the same size. Fields always live in view coordinates (points), so they
line up with what the user sees and with what export writes.
"""

from __future__ import annotations

import cv2
import numpy as np

from scan2form.core.model import BBox, Orientation, PageImage
from scan2form.core.preprocess import MIN_CORRECTION_DEG, estimate_skew


def view_size(base_w: float, base_h: float, o: Orientation) -> tuple[float, float]:
    return (base_h, base_w) if o.quarter_turns % 2 else (base_w, base_h)


def _matrix(base_w: float, base_h: float, o: Orientation) -> np.ndarray:
    """3x3 affine base -> view in any unit (points or pixels), as long as sizes match."""
    q = np.eye(3)
    w, h = base_w, base_h
    for _ in range(o.quarter_turns % 4):  # one clockwise turn: (x, y) -> (h - y, x)
        q = np.array([[0.0, -1.0, h], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]) @ q
        w, h = h, w
    fine = np.vstack(
        [cv2.getRotationMatrix2D((w / 2, h / 2), o.straighten_deg, 1.0), [0.0, 0.0, 1.0]]
    )
    return fine @ q


def render_view(base: np.ndarray, o: Orientation) -> np.ndarray:
    """Pixels of the view; uncovered corners become white."""
    turned = np.ascontiguousarray(np.rot90(base, k=-(o.quarter_turns % 4)))
    if abs(o.straighten_deg) < 1e-6:
        return turned
    h, w = turned.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), o.straighten_deg, 1.0)
    border = (255,) * (turned.shape[2] if turned.ndim == 3 else 1)
    return cv2.warpAffine(turned, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=border)


def remap_bbox(
    bbox: BBox, base_w: float, base_h: float, old: Orientation, new: Orientation
) -> BBox:
    """Move a box from the old view to the new one: centre mapped exactly, size kept
    (width/height swap on an odd number of quarter turns)."""
    to_base = np.linalg.inv(_matrix(base_w, base_h, old))
    cx, cy, _ = (
        _matrix(base_w, base_h, new)
        @ to_base
        @ [(bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2, 1.0]
    )
    w, h = bbox.width, bbox.height
    if (new.quarter_turns - old.quarter_turns) % 2:
        w, h = h, w
    return BBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def apply_orientation(
    page: PageImage, new: Orientation, extra_boxes: list | None = None
) -> PageImage:
    """Re-render `page` in place from its base in the new orientation; fields move along.

    In place, because the document and the UI scene share the PageImage. `extra_boxes`
    are objects with a `bbox` attribute (e.g. OCR spans) to move as well; for frozen
    objects use `remap_bbox` directly.
    """
    new = new.normalized()
    old = page.orientation
    base_w, base_h = view_size(page.width_pt, page.height_pt, old)  # undo the old swap
    for item in [*page.fields, *(extra_boxes or [])]:
        item.bbox = remap_bbox(item.bbox, base_w, base_h, old, new)
    page.base = page.base_pixels
    page.pixels = render_view(page.base, new)
    page.width_pt, page.height_pt = view_size(base_w, base_h, new)
    page.orientation = new
    return page


def auto_angle(page: PageImage) -> float:
    """Fine straightening angle for a page, measured on its base render.

    Quarter turns do not change it: rotations commute, so the tilt measured on the
    unturned page is the tilt of every turned view.
    """
    angle = estimate_skew(cv2.cvtColor(page.base_pixels, cv2.COLOR_RGB2GRAY))
    page.skew_deg = angle
    return -angle if abs(angle) >= MIN_CORRECTION_DEG else 0.0
