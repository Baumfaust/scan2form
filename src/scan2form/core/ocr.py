"""Local OCR with RapidOCR (Apache-2.0, ONNX runtime).

PP-OCRv5 text detection + the `latin` recognition model, which covers German
umlauts and ß. Models are fetched once on first use and cached by RapidOCR;
the app must bundle them for a fully offline install (milestone 5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scan2form.core.model import BBox, PageImage

MIN_SCORE = 0.5


@dataclass(frozen=True)
class TextSpan:
    text: str
    bbox: BBox  # view points
    score: float


class Ocr:
    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

            self._engine = RapidOCR(
                params={
                    "Global.log_level": "warning",
                    "Det.lang_type": LangDet.CH,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.MOBILE,
                    "Rec.lang_type": LangRec.LATIN,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                }
            )
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
