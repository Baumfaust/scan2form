import pytest

from scan2form.core.coords import PageGeometry
from scan2form.core.model import BBox


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_view_pdf_roundtrip(rotate):
    geometry = PageGeometry(10, 20, 605, 862, rotate)
    bbox = BBox(50, 100, 150, 120)
    rect = geometry.view_bbox_to_pdf_rect(bbox)
    back = geometry.pdf_rect_to_view_bbox(rect)
    assert back == pytest.approx(bbox)


def test_unrotated_page_flips_y_and_applies_cropbox_offset():
    geometry = PageGeometry(10, 20, 605, 862)
    assert geometry.view_bbox_to_pdf_rect(BBox(0, 0, 100, 50)) == (10, 812, 110, 862)


def test_rotated_90_top_left_maps_to_pdf_bottom_left():
    geometry = PageGeometry(0, 0, 595, 842, 90)
    assert geometry.view_to_pdf(0, 0) == (0, 0)
    assert geometry.view_to_pdf(842, 0) == (0, 842)  # displayed width is the PDF height
