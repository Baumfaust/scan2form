"""Ground truth for evaluation.

Two sources:
  * an already-fillable PDF: its widget annotations are the truth, and the page
    is rendered with annotations hidden so detectors see the flat form;
  * a sidecar `<file>.gt.json` (for scans/images), format:
      {"pages": [{"page": 0, "fields": [{"type": "text", "bbox": [x0, y0, x1, y1]}]}]}
    with bbox in points, top-left origin (the core coordinate convention).
"""

from __future__ import annotations

import json
from pathlib import Path

from pypdf import PdfReader

from scan2form.core.coords import PageGeometry
from scan2form.core.model import BBox, Field, FieldType
from scan2form.core.pdfform import (
    ANNOT_HIDDEN,
    FF_MULTILINE,
    FF_PUSHBUTTON,
    FF_RADIO,
    inherited,
    on_states,
    qualified_name,
)


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".gt.json")


def load_ground_truth(path: Path) -> dict[int, list[Field]] | None:
    """Return fields per page index, or None if the file has no ground truth."""
    sidecar = sidecar_path(path)
    if sidecar.exists():
        return _load_sidecar(sidecar)
    if path.suffix.lower() == ".pdf":
        fields = read_pdf_fields(path)
        return fields if any(fields.values()) else None
    return None


def _load_sidecar(path: Path) -> dict[int, list[Field]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(page["page"]): [
            Field(FieldType(f["type"]), BBox(*f["bbox"]), multiline=f.get("multiline", False))
            for f in page["fields"]
        ]
        for page in data["pages"]
    }


def read_pdf_fields(path: Path) -> dict[int, list[Field]]:
    """Widgets of a fillable PDF as fields per page, named by their qualified field name;
    radio widgets become checkboxes sharing the group name, with their state in `choice`."""
    reader = PdfReader(path)
    result: dict[int, list[Field]] = {}
    for index, page in enumerate(reader.pages):
        fields: list[Field] = []
        geometry = PageGeometry.from_pypdf(page)
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            if int(annot.get("/F", 0)) & ANNOT_HIDDEN:
                continue
            ft = inherited(annot, "/FT")
            flags = int(inherited(annot, "/Ff") or 0)
            if ft == "/Tx":
                field_type = FieldType.TEXT
            elif ft == "/Btn" and not flags & FF_PUSHBUTTON:
                field_type = FieldType.CHECKBOX  # radio widgets count as choice boxes
            elif ft == "/Sig":
                field_type = FieldType.SIGNATURE
            elif ft == "/Ch":
                field_type = FieldType.TEXT  # dropdowns look like text boxes on paper
            else:
                continue
            bbox = geometry.pdf_rect_to_view_bbox(annot["/Rect"])
            if bbox.width < 1 or bbox.height < 1:
                continue
            multiline = field_type is FieldType.TEXT and bool(flags & FF_MULTILINE)
            choice = None
            if ft == "/Btn" and flags & FF_RADIO:
                states = on_states(annot)
                choice = states[0].lstrip("/") if states else None
            fields.append(Field(field_type, bbox, multiline=multiline,
                                name=qualified_name(annot) or None, choice=choice))  # fmt: skip
        result[index] = fields
    return result
