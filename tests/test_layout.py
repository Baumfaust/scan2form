from scan2form.core.layout import (
    align_bottom,
    align_left,
    distribute_vertically,
    is_column,
    make_uniform,
    same_size,
)
from scan2form.core.model import BBox

# Three hand-drawn fields in a column, slightly sloppy; the last one is an outlier in width.
COLUMN = [BBox(100, 100, 300, 118), BBox(102, 140, 298, 157), BBox(99, 181, 380, 200)]


def test_same_size_uses_median_and_keeps_bottom_left():
    result = same_size(COLUMN)
    assert {(round(b.width), round(b.height)) for b in result} == {(200, 18)}
    assert [(b.x0, b.y1) for b in result] == [(b.x0, b.y1) for b in COLUMN]


def test_align_left_uses_median_left_edge():
    assert {b.x0 for b in align_left(COLUMN)} == {100}
    assert [b.width for b in align_left(COLUMN)] == [b.width for b in COLUMN]


def test_align_bottom_for_a_row():
    row = [BBox(0, 10, 50, 30), BBox(60, 12, 110, 31), BBox(120, 9, 170, 29)]
    assert {b.y1 for b in align_bottom(row)} == {30}


def test_make_uniform_detects_column_and_row():
    column = make_uniform(COLUMN)
    assert is_column(COLUMN)
    assert len({(b.x0, b.x1, round(b.height, 6)) for b in column}) == 1

    row = [BBox(0, 10, 50, 30), BBox(60, 12, 115, 31), BBox(120, 9, 168, 29)]
    assert not is_column(row)
    uniform_row = make_uniform(row)
    assert len({(round(b.y0, 6), b.y1, round(b.width, 6)) for b in uniform_row}) == 1


def test_distribute_vertically_keeps_ends_and_evens_gaps():
    boxes = [BBox(0, 0, 10, 10), BBox(0, 50, 10, 60), BBox(0, 30, 10, 40), BBox(0, 90, 10, 100)]
    result = distribute_vertically(boxes)
    assert sorted(b.y0 for b in result) == [0, 30, 60, 90]
    assert result[0].y0 == 0 and result[3].y0 == 90
    assert result[2].y0 == 30 and result[1].y0 == 60  # order by position is preserved
