import io
from dataclasses import astuple

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image
from pypdf import PdfReader

from scan2form.core.detect import OpenCVDetector
from scan2form.core.evaluate import match
from scan2form.core.export import export_pdf
from scan2form.core.groundtruth import load_ground_truth
from scan2form.core.ingest import load_pages, scan_info
from scan2form.core.model import BBox, Orientation
from scan2form.core.pipeline import load_project, process, save_project
from scan2form.core.preprocess import estimate_skew
from scan2form.core.straighten import apply_orientation, remap_bbox, render_view

TILT_DEG = 2.0  # content rotated counter-clockwise, like a sheet fed in crooked


@pytest.fixture
def crooked_scan(flat_form, tmp_path):
    """Image-only PDF (no text objects) of the synthetic form, tilted by TILT_DEG, at 200 dpi."""
    page = next(load_pages(flat_form, dpi=200))
    h, w = page.pixels.shape[:2]
    tilted = cv2.warpAffine(page.pixels, cv2.getRotationMatrix2D((w / 2, h / 2), TILT_DEG, 1.0), (w, h),
                            borderValue=(255, 255, 255))  # fmt: skip
    path = tmp_path / "crooked.pdf"
    Image.fromarray(tilted).convert("L").save(path, "PDF", resolution=200)
    return path


def _render(path, index=0, dpi=150):
    pdf = pdfium.PdfDocument(path)
    try:
        return np.asarray(
            pdf[index].render(scale=dpi / 72, draw_annots=False).to_pil().convert("L")
        )
    finally:
        pdf.close()


def test_scan_detection(flat_form, crooked_scan):
    assert scan_info(pdfium.PdfDocument(flat_form)[0]) == (False, None)
    scanned, native = scan_info(pdfium.PdfDocument(crooked_scan)[0])
    assert scanned and native == pytest.approx(200 / 72, rel=0.01)


@pytest.mark.parametrize("old", [Orientation(0, 0), Orientation(1, 3.5), Orientation(3, -7)])
@pytest.mark.parametrize("new", [Orientation(0, 2), Orientation(2, 0), Orientation(1, -1.5)])
def test_remap_roundtrip(old, new):
    box = BBox(100, 200, 260, 220)
    there = remap_bbox(box, 595, 842, old, new)
    back = remap_bbox(there, 595, 842, new, old)
    assert astuple(back) == pytest.approx(astuple(box), abs=1e-6)


def test_quarter_turn_moves_top_left_to_top_right_and_swaps_size(flat_form):
    page = next(load_pages(flat_form, dpi=50))
    w, h = page.width_pt, page.height_pt
    from scan2form.core.model import Field, FieldType

    page.fields = [Field(FieldType.TEXT, BBox(10, 20, 110, 40))]
    apply_orientation(page, Orientation(1, 0))
    assert (page.width_pt, page.height_pt) == (h, w)
    assert page.pixels.shape[:2] == page.base.shape[:2][::-1]
    f = page.fields[0].bbox
    assert (round(f.width), round(f.height)) == (20, 100)
    assert f.x0 > page.width_pt / 2 and f.y0 < page.height_pt / 2  # now top right


def test_pipeline_straightens_scan_and_finds_upright_fields(crooked_scan, synthetic_form):
    document = process(crooked_scan, OpenCVDetector(), ocr=None)
    page = document.pages[0]
    assert page.scanned
    assert page.orientation.straighten_deg == pytest.approx(-TILT_DEG, abs=0.3)
    assert abs(estimate_skew(cv2.cvtColor(page.pixels, cv2.COLOR_RGB2GRAY))) < 0.3
    score = match(page.fields, load_ground_truth(synthetic_form)[0], iou_threshold=0.7)
    assert score.recall == 1.0 and score.precision == 1.0


def test_export_writes_straight_page_with_aligned_fields(crooked_scan, tmp_path):
    document = process(crooked_scan, OpenCVDetector(), ocr=None)
    out = tmp_path / "out.pdf"
    export_pdf(crooked_scan, document.pages, out)

    assert abs(estimate_skew(_render(out))) < 0.3  # the page itself is upright now
    reader = PdfReader(out)
    assert len(reader.pages) == 1
    assert float(reader.pages[0].mediabox.width) == pytest.approx(document.pages[0].width_pt, abs=1)
    exported = load_ground_truth(out)[0]
    assert match(exported, document.pages[0].fields, iou_threshold=0.95).recall == 1.0
    # Fields sit on the printed boxes of the *exported* page.
    redetected = OpenCVDetector().detect(next(load_pages(out, dpi=150)))
    assert match(redetected, exported, iou_threshold=0.8).recall == 1.0


def test_export_keeps_scan_resolution(crooked_scan, tmp_path):
    document = process(crooked_scan, OpenCVDetector(), ocr=None)  # works at 150 dpi
    out = tmp_path / "out.pdf"
    export_pdf(crooked_scan, document.pages, out)
    (image,) = PdfReader(out).pages[0].images
    with Image.open(io.BytesIO(image.data)) as img:
        assert img.width == pytest.approx(210 / 25.4 * 200, rel=0.02)  # 200 dpi like the scan
        assert img.mode == "L"


def test_digital_page_turns_losslessly(flat_form, tmp_path):
    document = process(flat_form, OpenCVDetector(), ocr=None)
    page = document.pages[0]
    apply_orientation(page, Orientation(1, 0))
    out = tmp_path / "out.pdf"
    export_pdf(flat_form, document.pages, out)

    reader = PdfReader(out)
    assert reader.pages[0].get("/Rotate") == 90
    assert not reader.pages[0].images  # still vector content, nothing rasterised
    readback = load_ground_truth(out)[0]
    assert match(readback, page.fields, iou_threshold=0.95).recall == 1.0


def test_project_keeps_orientation(crooked_scan, tmp_path):
    document = process(crooked_scan, OpenCVDetector(), ocr=None)
    apply_orientation(
        document.pages[0], Orientation(2, document.pages[0].orientation.straighten_deg)
    )
    path = tmp_path / "p.s2f.json"
    save_project(document, path)

    restored = load_project(path)
    assert restored.pages[0].orientation == document.pages[0].orientation
    flat = [v for f in restored.pages[0].fields for v in astuple(f.bbox)]
    assert flat == pytest.approx([v for f in document.pages[0].fields for v in astuple(f.bbox)])


def test_render_view_identity_and_turns():
    base = np.zeros((4, 6, 3), np.uint8)
    base[0, 0] = 255  # top-left marker
    assert render_view(base, Orientation()).shape == (4, 6, 3)
    turned = render_view(base, Orientation(1, 0))
    assert turned.shape == (6, 4, 3) and turned[0, -1, 0] == 255  # clockwise: top-left -> top-right
