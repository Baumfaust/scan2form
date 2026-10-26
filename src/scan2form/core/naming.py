"""Rule-based field naming from nearby OCR labels."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from scan2form.core.model import BBox, Field, FieldType
from scan2form.core.ocr import TextSpan

PT_PER_MM = 72.0 / 25.4
MAX_NAME_LENGTH = 40
SIGNATURE_LABEL = re.compile(r"unterschrift|signatur|signature|sign here", re.IGNORECASE)
_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


@dataclass(frozen=True)
class Label:
    text: str
    bbox: BBox


def slugify(text: str) -> str:
    """'Straße / Hausnr.:' -> 'strasse_hausnr'. Field names stay ASCII for compatibility."""
    text = text.lower().translate(_TRANSLITERATION)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if len(slug) > MAX_NAME_LENGTH:
        slug = slug[:MAX_NAME_LENGTH].rsplit("_", 1)[0]
    return slug


def group_labels(spans: list[TextSpan], pt_per_mm: float = PT_PER_MM) -> list[Label]:
    """Join word-level OCR spans into phrases (same row, small gaps)."""
    labels: list[Label] = []
    for span in sorted(spans, key=lambda s: (s.bbox.y0, s.bbox.x0)):
        for i, label in enumerate(labels):
            b, s = label.bbox, span.bbox
            overlap = min(b.y1, s.y1) - max(b.y0, s.y0)
            same_row = overlap > 0.5 * min(b.height, s.height)
            gap = s.x0 - b.x1
            if same_row and -1 <= gap <= max(1.2 * s.height, 2 * pt_per_mm):
                labels[i] = Label(
                    f"{label.text} {span.text}",
                    BBox(min(b.x0, s.x0), min(b.y0, s.y0), max(b.x1, s.x1), max(b.y1, s.y1)),
                )
                break
        else:
            labels.append(Label(span.text, span.bbox))
    return labels


def _vertical_overlap(a: BBox, b: BBox) -> float:
    return min(a.y1, b.y1) - max(a.y0, b.y0)


def _horizontal_overlap(a: BBox, b: BBox) -> float:
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def find_label(field: Field, labels: list[Label], pt_per_mm: float = PT_PER_MM) -> Label | None:
    """Best label for a field; lower cost wins. Costs are distances in mm plus a position penalty."""
    f = field.bbox
    mm = pt_per_mm
    best: tuple[float, Label] | None = None

    def consider(cost: float, label: Label) -> None:
        nonlocal best
        if best is None or cost < best[0]:
            best = (cost, label)

    for label in labels:
        b = label.bbox
        row = _vertical_overlap(f, b) > 0.3 * min(f.height, b.height)
        if b.x0 >= f.x0 - mm and b.x1 <= f.x1 + mm and b.y0 >= f.y0 - mm and b.y1 <= f.y1 + mm:
            consider(0.0, label)  # caption printed inside the box
        elif field.type is FieldType.CHECKBOX:
            # OCR often reads the printed square as part of the word ("□ Ledig"),
            # so a label may start on top of (or just left of) the checkbox; it is the
            # label on the right as long as most of it lies right of the box.
            on_box = b.x0 < f.x1 and (b.x1 - f.x1) > 2 * max(0.0, f.x0 - b.x0)
            if row and (b.x0 >= f.x0 - 2 * mm or on_box) and b.x1 > f.x1 and b.x0 - f.x1 <= 30 * mm:
                consider(max(0.0, b.x0 - f.x1) / mm, label)
            elif row and -2 * mm <= f.x0 - b.x1 <= 30 * mm:
                consider(10 + max(0.0, f.x0 - b.x1) / mm, label)
        else:
            if row and -2 * mm <= f.x0 - b.x1 <= 60 * mm:
                consider((f.x0 - b.x1) / mm, label)
            elif (
                0 <= f.y0 - b.y1 <= 10 * mm
                and _horizontal_overlap(f, b) > 0
                and b.x0 <= f.x0 + 20 * mm
            ):
                consider(15 + (f.y0 - b.y1) / mm, label)
            elif -mm <= b.y0 - f.y1 <= 6 * mm and _horizontal_overlap(f, b) > 0.3 * b.width:
                # Caption under a line ("Ort, Datum"); OCR boxes may touch the line.
                consider(25 + max(0.0, b.y0 - f.y1) / mm, label)
    return best[1] if best else None


def assign_names(
    fields: list[Field],
    spans: list[TextSpan],
    page_index: int,
    taken: set[str],
    pt_per_mm: float = PT_PER_MM,
) -> None:
    """Set `name` on every field (unique across `taken`, which is updated) and detect signatures."""
    labels = group_labels(spans, pt_per_mm)
    ordered = sorted(fields, key=lambda f: (round(f.bbox.y0 / (3 * pt_per_mm)), f.bbox.x0))
    for number, field in enumerate(ordered, start=1):
        label = find_label(field, labels, pt_per_mm)
        if label and field.type is FieldType.TEXT and SIGNATURE_LABEL.search(label.text):
            field.type = FieldType.SIGNATURE
        base = slugify(label.text) if label else ""
        if not base:  # generic name: next free number on this page, e.g. text_p2_7
            n = number
            while f"{field.type.value}_p{page_index + 1}_{n}" in taken:
                n += 1
            base = f"{field.type.value}_p{page_index + 1}_{n}"
        elif base[0].isdigit():
            base = f"f_{base}"
        name, suffix = base, 2
        while name in taken:
            name, suffix = f"{base}_{suffix}", suffix + 1
        taken.add(name)
        field.name = name
