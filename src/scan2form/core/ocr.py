"""Local OCR with RapidOCR (Apache-2.0, ONNX runtime).

PP-OCRv5 text detection + the `latin` recognition model, which covers German
umlauts and ß. In a development install RapidOCR fetches the models once on first
use; the packaged app ships them in `ocr-models/` and loads them from there, so it
never needs a connection.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scan2form.core.model import BBox, PageImage

MIN_SCORE = 0.5
MODEL_FILES = {  # RapidOCR section -> model file (as RapidOCR names them when downloading)
    "Det": "ch_PP-OCRv5_det_mobile.onnx",
    "Rec": "latin_PP-OCRv5_rec_mobile.onnx",
    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
}


def bundled_models_dir() -> Path | None:
    """`ocr-models/` of a packaged app (PyInstaller), if it holds all model files."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    folder = Path(base) / "ocr-models"
    return folder if all((folder / name).is_file() for name in MODEL_FILES.values()) else None


@dataclass(frozen=True)
class TextSpan:
    text: str
    bbox: BBox  # view points
    score: float


class Ocr:
    def __init__(self, models_dir: Path | None = None) -> None:
        self._engine = None
        self.models_dir = models_dir or bundled_models_dir()

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

            params = {
                "Global.log_level": "warning",
                "Det.lang_type": LangDet.CH,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Rec.lang_type": LangRec.LATIN,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            }
            if self.models_dir is not None:  # explicit files: RapidOCR never downloads
                for section, name in MODEL_FILES.items():
                    params[f"{section}.model_path"] = str(self.models_dir / name)
            self._engine = RapidOCR(params=params)
        return self._engine

    def read(self, page: PageImage) -> list[TextSpan]:
        result = self._get_engine()(page.pixels)
        if result.boxes is None:
            return []
        to_pt = 1.0 / page.px_per_pt
        spans = []
        for quad, text, score in zip(result.boxes, result.txts, result.scores):
            if float(score) < MIN_SCORE or not text.strip():
                continue
            quad = np.asarray(quad, dtype=float)
            bbox = BBox(*quad.min(axis=0), *quad.max(axis=0)).scaled(to_pt)
            spans.append(TextSpan(text.strip(), bbox, float(score)))
        return spans
