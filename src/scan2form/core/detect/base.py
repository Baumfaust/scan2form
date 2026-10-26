"""Detector interface shared by all field detection backends."""

from __future__ import annotations

from typing import Protocol

from scan2form.core.model import Field, PageImage


class Detector(Protocol):
    name: str

    def detect(self, page: PageImage) -> list[Field]:
        """Return field candidates in page points (top-left origin)."""
        ...


def non_max_suppression(fields: list[Field], iou_threshold: float = 0.5) -> list[Field]:
    """Greedy class-agnostic NMS: keep the higher-scoring box of each overlapping pair."""
    kept: list[Field] = []
    for candidate in sorted(fields, key=lambda f: f.score, reverse=True):
        if all(candidate.bbox.iou(k.bbox) < iou_threshold for k in kept):
            kept.append(candidate)
    return kept
