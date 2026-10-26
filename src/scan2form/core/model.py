"""Core data model.

Coordinate convention: all boxes are in PDF points (1/72 inch) relative to the
page's top-left corner, y pointing down. Conversion to PDF's bottom-left origin
happens only at the PDF boundary (ground truth import, export).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


class FieldType(StrEnum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    SIGNATURE = "signature"


@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def iou(self, other: BBox) -> float:
        ix = max(0.0, min(self.x1, other.x1) - max(self.x0, other.x0))
        iy = max(0.0, min(self.y1, other.y1) - max(self.y0, other.y0))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def scaled(self, factor: float) -> BBox:
        return BBox(self.x0 * factor, self.y0 * factor, self.x1 * factor, self.y1 * factor)


@dataclass
class Field:
    type: FieldType
    bbox: BBox
    score: float = 1.0
    multiline: bool = False
    name: str | None = None
    choice: str | None = None  # radio button: the state this widget sets; widgets share `name`


PT_PER_MM = 72.0 / 25.4
MAX_FINE_DEG = 10.0


@dataclass(frozen=True)
class Orientation:
    """How a page is shown and exported: clockwise quarter turns, then a fine straightening
    angle (OpenCV convention, positive = counter-clockwise). See core.straighten."""

    quarter_turns: int = 0
    straighten_deg: float = 0.0

    def normalized(self) -> Orientation:
        fine = max(-MAX_FINE_DEG, min(MAX_FINE_DEG, round(self.straighten_deg, 2)))
        return Orientation(self.quarter_turns % 4, fine)

    @property
    def is_identity(self) -> bool:
        return self.quarter_turns % 4 == 0 and abs(self.straighten_deg) < 1e-6


@dataclass
class PageImage:
    """A rasterised page. `pixels` is RGB uint8, `px_per_pt` maps points to pixels.

    `pt_per_mm` is the page's physical scale. It differs from the nominal 72/25.4
    for scans saved with one point per pixel (see ingest.physical_pt_per_mm).
    `pixels`, `width_pt`, `height_pt` and all field boxes describe the page *as shown*,
    i.e. after `orientation`; `base` keeps the unrotated render to re-orient losslessly.
    """

    index: int
    pixels: np.ndarray
    px_per_pt: float
    width_pt: float
    height_pt: float
    pt_per_mm: float = PT_PER_MM
    skew_deg: float = 0.0
    fields: list[Field] = field(default_factory=list)
    scanned: bool = False  # image-only page: may be re-encoded straightened on export
    native_px_per_pt: float | None = None  # resolution of the scan image itself (export quality)
    orientation: Orientation = Orientation()
    base: np.ndarray | None = None  # unrotated source render; None = pixels are the base

    @property
    def base_pixels(self) -> np.ndarray:
        return self.pixels if self.base is None else self.base

    @property
    def px_per_mm(self) -> float:
        return self.px_per_pt * self.pt_per_mm
