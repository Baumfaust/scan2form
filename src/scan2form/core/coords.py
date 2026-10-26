"""The single place where view coordinates meet PDF coordinates.

View space: points, origin top-left of the page *as displayed* (after /Rotate
and CropBox), y down. That is what pdfium renders and what the core uses.
PDF space: user-space points, origin bottom-left of the unrotated page.
"""

from __future__ import annotations

from dataclasses import dataclass

from scan2form.core.model import BBox


@dataclass(frozen=True)
class PageGeometry:
    left: float
    bottom: float
    right: float
    top: float
    rotate: int = 0  # /Rotate, normalised to 0/90/180/270

    @classmethod
    def from_pypdf(cls, page) -> PageGeometry:
        box = page.cropbox
        rotate = int(page.get("/Rotate", 0) or 0) % 360
        return cls(float(box.left), float(box.bottom), float(box.right), float(box.top), rotate)

    def view_to_pdf(self, u: float, v: float) -> tuple[float, float]:
        if self.rotate == 90:
            return self.left + v, self.bottom + u
        if self.rotate == 180:
            return self.right - u, self.bottom + v
        if self.rotate == 270:
            return self.right - v, self.top - u
        return self.left + u, self.top - v

    def pdf_to_view(self, x: float, y: float) -> tuple[float, float]:
        if self.rotate == 90:
            return y - self.bottom, x - self.left
        if self.rotate == 180:
            return self.right - x, y - self.bottom
        if self.rotate == 270:
            return self.top - y, self.right - x
        return x - self.left, self.top - y

    def view_bbox_to_pdf_rect(self, bbox: BBox) -> tuple[float, float, float, float]:
        xa, ya = self.view_to_pdf(bbox.x0, bbox.y0)
        xb, yb = self.view_to_pdf(bbox.x1, bbox.y1)
        return min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)

    def pdf_rect_to_view_bbox(self, rect) -> BBox:
        x0, y0, x1, y1 = (float(v) for v in rect)
        ua, va = self.pdf_to_view(x0, y0)
        ub, vb = self.pdf_to_view(x1, y1)
        return BBox(min(ua, ub), min(va, vb), max(ua, ub), max(va, vb))
