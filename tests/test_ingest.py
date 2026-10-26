import pytest
from reportlab.pdfgen import canvas

from scan2form.core.detect import OpenCVDetector
from scan2form.core.ingest import load_pages, physical_pt_per_mm
from scan2form.core.model import PT_PER_MM, FieldType


def test_normal_pages_keep_nominal_scale():
    assert physical_pt_per_mm(595, 842) == PT_PER_MM


def test_one_point_per_pixel_scan_is_matched_to_a4():
    # A4 at 200 dpi stored as 1653 x 2338 "points"
    assert physical_pt_per_mm(1653, 2338) == pytest.approx(200 / 25.4, rel=0.01)


def test_unknown_aspect_falls_back_to_nominal():
    assert physical_pt_per_mm(2000, 2100) == PT_PER_MM


def test_detector_uses_physical_scale_on_oversized_scan(tmp_path):
    """A 1pt-per-pixel A4 page with a 5 mm checkbox must still yield a checkbox."""
    path = tmp_path / "scan_like.pdf"
    mm = 200 / 25.4  # points per mm on this page
    c = canvas.Canvas(str(path), pagesize=(1653, 2338))
    c.setLineWidth(0.3 * mm)
    c.rect(30 * mm, 2338 - 50 * mm, 5 * mm, 5 * mm)
    c.save()

    page = next(load_pages(path, dpi=150))
    assert page.pixels.shape[1] == pytest.approx(210 / 25.4 * 150, abs=3)

    fields = OpenCVDetector().detect(page)
    assert [f.type for f in fields] == [FieldType.CHECKBOX]
    assert fields[0].bbox.width == pytest.approx(5 * mm, rel=0.15)  # in the PDF's own points
