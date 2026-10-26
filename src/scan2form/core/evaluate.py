"""Precision/recall of detected fields against ground truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from scan2form.core.model import Field


@dataclass
class Score:
    true_positives: int = 0
    predicted: int = 0
    expected: int = 0
    type_correct: int = 0

    def __iadd__(self, other: Score) -> Self:
        self.true_positives += other.true_positives
        self.predicted += other.predicted
        self.expected += other.expected
        self.type_correct += other.type_correct
        return self

    @property
    def precision(self) -> float:
        return self.true_positives / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.expected if self.expected else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def type_accuracy(self) -> float:
        """Share of matched fields that also got the right type."""
        return self.type_correct / self.true_positives if self.true_positives else 0.0


def loose_overlap(predicted: Field, expected: Field, max_dy: float) -> float:
    """Share of the expected width covered by the prediction, 0 unless bottoms are within `max_dy`.

    Suited to hand-marked ground truth (underlines), where the exact box height
    and extent are guesses but "is there a field on this line" is reliable.
    """
    p, e = predicted.bbox, expected.bbox
    if abs(p.y1 - e.y1) > max_dy or e.width <= 0:
        return 0.0
    return max(0.0, min(p.x1, e.x1) - max(p.x0, e.x0)) / e.width


def match(
    predicted: list[Field],
    expected: list[Field],
    iou_threshold: float = 0.5,
    loose_max_dy: float | None = None,
) -> Score:
    """Greedy one-to-one matching, class-agnostic; type agreement tracked separately.

    By default pairs are scored by IoU. With `loose_max_dy` (in points) they are
    scored by `loose_overlap` instead, with `iou_threshold` as the minimum coverage.
    """

    def similarity(p: Field, e: Field) -> float:
        return p.bbox.iou(e.bbox) if loose_max_dy is None else loose_overlap(p, e, loose_max_dy)

    pairs = sorted(
        (
            (similarity(p, e), pi, ei)
            for pi, p in enumerate(predicted)
            for ei, e in enumerate(expected)
        ),
        reverse=True,
    )
    used_p: set[int] = set()
    used_e: set[int] = set()
    score = Score(predicted=len(predicted), expected=len(expected))
    for iou, pi, ei in pairs:
        if iou < iou_threshold:
            break
        if pi in used_p or ei in used_e:
            continue
        used_p.add(pi)
        used_e.add(ei)
        score.true_positives += 1
        if predicted[pi].type is expected[ei].type:
            score.type_correct += 1
    return score
