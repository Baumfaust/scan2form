from scan2form.core.evaluate import match
from scan2form.core.model import BBox, Field, FieldType


def field(x0, y0, x1, y1, t=FieldType.TEXT):
    return Field(t, BBox(x0, y0, x1, y1))


def test_one_to_one_matching_and_metrics():
    expected = [field(0, 0, 10, 10), field(20, 0, 30, 10, FieldType.CHECKBOX)]
    predicted = [
        field(0, 0, 10, 10),
        field(1, 1, 10, 10),  # duplicate of the first: false positive
        field(20, 0, 30, 10),  # right place, wrong type
    ]
    score = match(predicted, expected)
    assert score.true_positives == 2
    assert score.precision == 2 / 3
    assert score.recall == 1.0
    assert score.type_accuracy == 0.5


def test_loose_matching_ignores_height_but_not_row():
    marked = [field(10, 90, 60, 100)]  # hand-marked underline, guessed height
    same_line_wider = [field(0, 80, 120, 102)]
    other_line = [field(0, 120, 120, 140)]
    assert match(same_line_wider, marked).true_positives == 0  # IoU too low
    assert match(same_line_wider, marked, loose_max_dy=10).true_positives == 1
    assert match(other_line, marked, loose_max_dy=10).true_positives == 0


def test_empty_inputs():
    score = match([], [])
    assert (score.precision, score.recall, score.f1) == (0.0, 0.0, 0.0)
