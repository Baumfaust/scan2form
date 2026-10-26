"""Optional FFDNet backend (CommonForms, github.com/jbarrow/commonforms).

LOCAL EVALUATION ONLY. The FFDNet weights and the commonforms code currently
have no license, and inference uses Ultralytics (AGPL-3.0). Nothing here is
bundled; weights are downloaded from Hugging Face on first use.
Install with: uv sync --group ffdnet-eval
"""

from __future__ import annotations

import os
from pathlib import Path

from scan2form.core.model import BBox, Field, FieldType, PageImage

HF_REPOS = {"FFDNet-S": "jbarrow/FFDNet-S", "FFDNet-L": "jbarrow/FFDNet-L"}


def _map_class(label: str) -> FieldType | None:
    label = label.lower()
    if "choice" in label or "check" in label:
        return FieldType.CHECKBOX
    if "sig" in label:
        return FieldType.SIGNATURE
    if "text" in label:
        return FieldType.TEXT
    return None


class FFDNetDetector:
    def __init__(
        self,
        variant: str = "FFDNet-L",
        image_size: int = 1216,
        confidence: float = 0.3,
        weights: Path | None = None,
    ) -> None:
        # Offline promise: no usage analytics from either library. Must be set before import.
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ["YOLO_OFFLINE"] = "True"
        try:
            from ultralytics import YOLO, settings

            settings.update({"sync": False})
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "FFDNet needs the optional eval dependencies: uv sync --group ffdnet-eval"
            ) from exc
        if weights is None:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import LocalEntryNotFoundError

            try:  # offline first: no network call once the weights are cached
                weights = Path(
                    hf_hub_download(HF_REPOS[variant], f"{variant}.pt", local_files_only=True)
                )
            except LocalEntryNotFoundError:
                weights = Path(hf_hub_download(HF_REPOS[variant], f"{variant}.pt"))
        self.name = variant.lower()
        self.image_size = image_size
        self.confidence = confidence
        self._model = YOLO(str(weights))

    def detect(self, page: PageImage) -> list[Field]:
        result = self._model.predict(
            page.pixels[:, :, ::-1],  # ultralytics expects BGR numpy arrays
            imgsz=self.image_size,
            conf=self.confidence,
            verbose=False,
        )[0]
        names = result.names
        scale = 1.0 / page.px_per_pt
        fields = []
        for xyxy, cls, conf in zip(
            result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), result.boxes.conf.tolist()
        ):
            field_type = _map_class(str(names[int(cls)]))
            if field_type is None:
                continue
            fields.append(Field(field_type, BBox(*xyxy).scaled(scale), score=float(conf)))
        return fields
