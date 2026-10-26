import cv2
import pytest

from scan2form.core.detect import OpenCVDetector
from scan2form.core.ingest import load_pages
from scan2form.core.model import BBox, PageImage
from scan2form.core.ocr import Ocr
from scan2form.core.pipeline import process
from scan2form.core.preprocess import deskew, unskew_bbox


def test_unskew_maps_deskewed_box_back_to_original(flat_form):
    page = next(load_pages(flat_form, dpi=150))
    h, w = page.pixels.shape[:2]
    rotated = cv2.warpAffine(
        page.pixels,
        cv2.getRotationMatrix2D((w / 2, h / 2), 2.0, 1.0),
        (w, h),
        borderValue=(255, 255, 255),
    )
    skewed = PageImage(0, rotated, page.px_per_pt, page.width_pt, page.height_pt)
    work = deskew(skewed)
    box = BBox(150, 122, 400, 142)  # the "Vorname" box in upright coordinates

    back = unskew_bbox(box, work)

    # Rotating the original page by +2 deg moves that box's centre accordingly.
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 2.0, 1.0)
    cx, cy = (box.x0 + box.x1) / 2 * page.px_per_pt, (box.y0 + box.y1) / 2 * page.px_per_pt
    ex, ey = matrix @ [cx, cy, 1.0]
    assert (back.x0 + back.x1) / 2 == pytest.approx(ex / page.px_per_pt, abs=1.5)
    assert (back.y0 + back.y1) / 2 == pytest.approx(ey / page.px_per_pt, abs=1.5)


def test_ocr_names_fields_from_german_labels(flat_form):
    """Runs the real local OCR models (downloaded once by RapidOCR)."""
    document = process(flat_form, OpenCVDetector(), ocr=Ocr())
    names = sorted(f.name for f in document.pages[0].fields)
    assert names == ["bemerkung", "ledig", "strasse", "verheiratet", "vorname"]
