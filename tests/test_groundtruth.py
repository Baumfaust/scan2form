import json

import pytest
from conftest import SYNTHETIC_FIELDS
from reportlab.lib.pagesizes import A4

from scan2form.core.groundtruth import load_ground_truth, sidecar_path
from scan2form.core.model import BBox, FieldType


def test_widgets_become_ground_truth_in_top_left_points(synthetic_form):
    gt = load_ground_truth(synthetic_form)
    fields = gt[0]
    assert len(fields) == len(SYNTHETIC_FIELDS)

    page_h = A4[1]
    by_pos = {(round(f.bbox.x0), round(f.bbox.y0)): f for f in fields}
    for kind, x, y, w, h, _label in SYNTHETIC_FIELDS:
        field = by_pos[(x, round(page_h - y - h))]
        assert field.bbox.width == pytest.approx(w, abs=0.5)
        assert field.bbox.height == pytest.approx(h, abs=0.5)
        expected = FieldType.CHECKBOX if kind == "checkbox" else FieldType.TEXT
        assert field.type is expected
        assert field.multiline == (h > 40)


def test_sidecar_overrides_pdf_widgets(synthetic_form):
    sidecar_path(synthetic_form).write_text(
        json.dumps(
            {"pages": [{"page": 0, "fields": [{"type": "signature", "bbox": [1, 2, 3, 4]}]}]}
        )
    )
    gt = load_ground_truth(synthetic_form)
    assert [(f.type, f.bbox) for f in gt[0]] == [(FieldType.SIGNATURE, BBox(1, 2, 3, 4))]


def test_image_without_sidecar_has_no_ground_truth(tmp_path):
    assert load_ground_truth(tmp_path / "scan.png") is None
