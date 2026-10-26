"""Arrange several fields: equal size, alignment, even spacing.

All functions take and return boxes in the same order. Reference values are
medians, so one sloppy hand-drawn box does not pull the others along.
Resizing keeps the bottom-left corner: fields usually sit on a printed line.
"""

from __future__ import annotations

from statistics import median

from scan2form.core.model import BBox


def same_size(boxes: list[BBox]) -> list[BBox]:
    w = median(b.width for b in boxes)
    h = median(b.height for b in boxes)
    return [BBox(b.x0, b.y1 - h, b.x0 + w, b.y1) for b in boxes]


def align_left(boxes: list[BBox]) -> list[BBox]:
    x = median(b.x0 for b in boxes)
    return [BBox(x, b.y0, x + b.width, b.y1) for b in boxes]


def align_bottom(boxes: list[BBox]) -> list[BBox]:
    y = median(b.y1 for b in boxes)
    return [BBox(b.x0, y - b.height, b.x1, y) for b in boxes]


def is_column(boxes: list[BBox]) -> bool:
    """True when the boxes are stacked (more vertical than horizontal spread)."""
    xs = [(b.x0 + b.x1) / 2 for b in boxes]
    ys = [(b.y0 + b.y1) / 2 for b in boxes]
    return max(ys) - min(ys) >= max(xs) - min(xs)


def make_uniform(boxes: list[BBox]) -> list[BBox]:
    """Same size, then align as a column (left edges) or a row (bottom edges)."""
    sized = same_size(boxes)
    return align_left(sized) if is_column(boxes) else align_bottom(sized)


def distribute_vertically(boxes: list[BBox]) -> list[BBox]:
    """Equal spacing between the tops; the topmost and bottommost box stay in place."""
    return _distribute(boxes, vertical=True)


def distribute_horizontally(boxes: list[BBox]) -> list[BBox]:
    return _distribute(boxes, vertical=False)


def _distribute(boxes: list[BBox], vertical: bool) -> list[BBox]:
    if len(boxes) < 3:
        return list(boxes)
    start = (lambda b: b.y0) if vertical else (lambda b: b.x0)
    order = sorted(range(len(boxes)), key=lambda i: start(boxes[i]))
    first, last = start(boxes[order[0]]), start(boxes[order[-1]])
    step = (last - first) / (len(boxes) - 1)
    result = list(boxes)
    for rank, i in enumerate(order):
        b, offset = boxes[i], first + rank * step - start(boxes[i])
        result[i] = (
            BBox(b.x0, b.y0 + offset, b.x1, b.y1 + offset)
            if vertical
            else BBox(b.x0 + offset, b.y0, b.x1 + offset, b.y1)
        )
    return result


ARRANGEMENTS = {
    "uniform": ("Make uniform", make_uniform),
    "same_size": ("Same size", same_size),
    "align_left": ("Align left edges", align_left),
    "align_bottom": ("Align bottom edges", align_bottom),
    "distribute_v": ("Distribute vertically", distribute_vertically),
    "distribute_h": ("Distribute horizontally", distribute_horizontally),
}
