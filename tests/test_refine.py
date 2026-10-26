from dataclasses import astuple

import pytest
from reportlab.lib.pagesizes import A4

from scan2form.core.ingest import load_pages
from scan2form.core.model import BBox, FieldType
from scan2form.core.refine import refine_mark

PAGE_H = A4[1]


def view(x, y, w, h):
    """reportlab rect (bottom-left origin) -> view bbox (top-left origin)."""
    return BBox(x, PAGE_H - y - h, x + w, PAGE_H - y)


@pytest.fixture
def page(flat_form):
    return next(load_pages(flat_form, dpi=150))


def test_rough_dab_snaps_to_printed_checkbox(page):
    field = refine_mark(page, BBox(201, PAGE_H - 513, 208, PAGE_H - 505))  # sloppy, off-centre
    assert field.type is FieldType.CHECKBOX
    assert field.bbox.iou(view(200, 500, 11, 11)) > 0.7


def test_short_stroke_extends_to_the_whole_printed_line(page):
    stroke = BBox(200, PAGE_H - 553, 260, PAGE_H - 549)  # covers only part of the 200 pt line
    field = refine_mark(page, stroke)
    assert field.type is FieldType.TEXT
    assert field.bbox.x0 == pytest.approx(150, abs=2)
    assert field.bbox.x1 == pytest.approx(350, abs=2)
    assert field.bbox.y1 == pytest.approx(PAGE_H - 550, abs=1.5)


def test_paint_inside_a_box_fills_the_box(page):
    field = refine_mark(page, BBox(200, PAGE_H - 660, 320, PAGE_H - 630))
    assert field.type is FieldType.TEXT
    assert field.multiline
    assert field.bbox.iou(view(150, 600, 300, 80)) > 0.9


def test_mark_on_empty_paper_is_kept_as_painted(page):
    mark = BBox(400, 100, 500, 140)
    field = refine_mark(page, mark)
    assert astuple(field.bbox) == pytest.approx(astuple(mark))
    assert field.score < 1.0  # flagged as not snapped
