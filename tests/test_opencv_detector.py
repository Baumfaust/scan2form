import cv2

from scan2form.core.detect import OpenCVDetector
from scan2form.core.evaluate import match
from scan2form.core.groundtruth import load_ground_truth
from scan2form.core.ingest import load_pages
from scan2form.core.model import FieldType, PageImage
from scan2form.core.preprocess import deskew


def test_finds_boxes_lines_and_checkboxes(synthetic_form):
    page = next(load_pages(synthetic_form, dpi=150, draw_annots=False))
    gt = load_ground_truth(synthetic_form)[0]

    predicted = OpenCVDetector().detect(page)
    score = match(predicted, gt, iou_threshold=0.5)

    assert score.recall == 1.0
    assert score.precision == 1.0
    assert score.type_accuracy == 1.0
    assert sum(f.type is FieldType.CHECKBOX for f in predicted) == 2
    assert sum(f.multiline for f in predicted) == 1


def test_deskew_recovers_rotation(synthetic_form):
    page = next(load_pages(synthetic_form, dpi=150))
    h, w = page.pixels.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 3.0, 1.0)
    rotated = cv2.warpAffine(page.pixels, matrix, (w, h), borderValue=(255, 255, 255))
    skewed = PageImage(0, rotated, page.px_per_pt, page.width_pt, page.height_pt)

    fixed = deskew(skewed)

    assert abs(fixed.skew_deg - 3.0) < 0.3
