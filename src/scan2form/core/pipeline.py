"""End-to-end processing: ingest -> deskew -> detect -> OCR -> naming."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from pypdf.errors import PdfReadError

from scan2form.core.detect import Detector
from scan2form.core.export import export_pdf
from scan2form.core.groundtruth import read_pdf_fields
from scan2form.core.ingest import PDF_SUFFIXES, count_pages, load_pages
from scan2form.core.model import BBox, Field, FieldType, Orientation, PageImage
from scan2form.core.naming import assign_names
from scan2form.core.ocr import Ocr, TextSpan
from scan2form.core.preprocess import deskew, unskew_bbox
from scan2form.core.straighten import apply_orientation, auto_angle

ProgressCallback = Callable[[int, int, str], None]  # (page index, page count, step)
STEPS = ("deskew", "detect", "ocr")  # per page, in this order


@dataclass
class Document:
    source: Path
    pages: list[PageImage]  # original (not deskewed) renders; fields in their coordinates
    spans: dict[int, list[TextSpan]] = field(
        default_factory=dict
    )  # OCR text per page, same coordinates
    # The source already had form fields (opened as a fillable PDF); export replaces them.
    has_source_fields: bool = False
    pristine: tuple | None = None  # signature() right after opening such a PDF

    @property
    def field_count(self) -> int:
        return sum(len(p.fields) for p in self.pages)

    def signature(self) -> tuple:
        """Everything that ends up in the form's structure (not the filled values)."""

        def field_key(f: Field) -> tuple:
            box = tuple(round(v, 1) for v in (f.bbox.x0, f.bbox.y0, f.bbox.x1, f.bbox.y1))
            return f.name, f.type, f.multiline, f.choice, box

        return tuple((p.orientation, tuple(map(field_key, p.fields))) for p in self.pages)

    @property
    def source_form_unchanged(self) -> bool:
        """True while the opened fillable PDF can be filled as it is (keeps radio groups,
        dropdowns, scripts and formatting of third-party forms)."""
        return self.pristine is not None and self.signature() == self.pristine


def has_form_fields(path: Path) -> bool:
    if path.suffix.lower() not in PDF_SUFFIXES:
        return False
    try:
        return any(read_pdf_fields(path).values())
    except (PdfReadError, OSError, ValueError, KeyError):  # damaged/encrypted: normal import
        return False


def open_fillable(
    source: Path, dpi: float = 150.0, progress: ProgressCallback | None = None
) -> Document:
    """Open a PDF that already has form fields: its fields as they are, no detection."""
    by_page = read_pdf_fields(source)
    page_count = count_pages(source)
    pages = []
    for page in load_pages(source, dpi=dpi):  # widgets hidden: values are shown by Scan2Form
        if progress:
            progress(page.index, page_count, "detect")
        page.fields = by_page.get(page.index, [])
        pages.append(page)
    document = Document(source, pages, has_source_fields=True)
    document.pristine = document.signature()
    return document


def link_across_pages(fields: list[Field], used: dict[str, FieldType]) -> None:
    """After naming one page: a text field whose label already named a text field on an
    earlier page keeps that name, i.e. becomes the same field ("Vorname" on every page is
    filled once). Other collisions (checkboxes like "ja", mixed types) get a suffix."""
    on_page = {f.name for f in fields}
    for f in fields:
        other = used.get(f.name)
        if other is None or (f.type is FieldType.TEXT and other is FieldType.TEXT):
            continue
        n = 2
        while f"{f.name}_{n}" in used or f"{f.name}_{n}" in on_page:
            n += 1
        f.name = f"{f.name}_{n}"
        on_page.add(f.name)
    for f in fields:
        used.setdefault(f.name, f.type)


def _kind(f: Field) -> tuple:
    return f.type, f.choice is not None


def name_conflicts(document: Document, name: str, field: Field) -> bool:
    """Whether `name` cannot be given to `field`: fields of the same kind may share a name
    (they become one linked field with one value); a checkbox and a text field may not."""
    return any(
        f.name == name and f is not field and _kind(f) != _kind(field)
        for p in document.pages
        for f in p.fields
    )


def linked_pages(document: Document, field: Field) -> list[int]:
    """1-based pages of the other fields sharing `field`'s name (radio groups excluded)."""
    if not field.name or field.choice is not None:
        return []
    return sorted({p.index + 1 for p in document.pages for f in p.fields
                   if f is not field and f.name == field.name})  # fmt: skip


_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<n>\d+)$")


def link_repeated_names(document: Document) -> list[tuple[Field, str]]:
    """(field, base name) for text fields named like "vorname_2" whose base name is a text
    field on another page but not on their own: the same label repeated per page."""
    pages_of: dict[str, set[int]] = {}
    for p in document.pages:
        for f in p.fields:
            if f.name and f.type is FieldType.TEXT:
                pages_of.setdefault(f.name, set()).add(p.index)
    kinds = {f.name: _kind(f) for p in document.pages for f in p.fields if f.name}
    renames = []
    for p in document.pages:
        names_here = {f.name for f in p.fields}
        for f in p.fields:
            m = _SUFFIX.match(f.name or "")
            if f.type is not FieldType.TEXT or m is None:
                continue
            base = m["base"]
            if (
                base in names_here
                or kinds.get(base) != _kind(f)
                or not pages_of.get(base, set()) - {p.index}
            ):
                continue
            renames.append((f, base))
            names_here.add(base)
    return renames


def ensure_unique_names(document: Document) -> list[tuple[Field, str]]:
    """(field, new name) for unnamed fields and names shared by fields of different kinds;
    fields of the same kind keep a shared name on purpose (linked fields, radio groups).
    Nothing is changed here, so the caller can make it undoable."""
    taken = {f.name for p in document.pages for f in p.fields if f.name}
    first_kind: dict[str, tuple] = {}
    renames = []
    for page in document.pages:
        for f in page.fields:
            if f.name and first_kind.setdefault(f.name, _kind(f)) == _kind(f):
                continue
            base, n = f.name or f"{f.type.value}_p{page.index + 1}", 1
            while f"{base}_{n}" in taken:
                n += 1
            name = f"{base}_{n}"
            taken.add(name)
            first_kind[name] = _kind(f)
            renames.append((f, name))
    return renames


def form_template(document: Document) -> Path | bytes:
    """The blank fillable form to fill: the opened PDF itself while its fields are
    unchanged, otherwise a fresh export (without any personal data)."""
    if document.source_form_unchanged:
        return document.source
    with tempfile.TemporaryDirectory() as tmp:
        template = Path(tmp) / "form.pdf"
        export_pdf(
            document.source, document.pages, template, replace_existing=document.has_source_fields
        )
        return template.read_bytes()


def process(
    source: Path,
    detector: Detector,
    ocr: Ocr | None = None,
    dpi: float = 150.0,
    progress: ProgressCallback | None = None,
) -> Document:
    page_count = count_pages(source)

    def report(index: int, step: str) -> None:
        if progress:
            progress(index, page_count, step)

    pages: list[PageImage] = []
    spans_by_page: dict[int, list[TextSpan]] = {}
    used: dict[str, FieldType] = {}  # names on earlier pages
    for original in load_pages(source, dpi=dpi):
        report(original.index, "deskew")
        if original.scanned:
            # Scans are straightened for real: fields, OCR and export all use the upright view.
            page = auto_straighten(original)
            work = page
        else:
            # Digital pages are never re-rasterised: detect on an upright copy, map back.
            page, work = original, deskew(original)
        report(original.index, "detect")
        fields = detector.detect(work)
        if ocr is not None:
            report(original.index, "ocr")
            spans = ocr.read(work)
        else:
            spans = []
        assign_names(fields, spans, original.index, set(), work.pt_per_mm)
        link_across_pages(fields, used)
        if work is not page:
            for f in fields:
                f.bbox = unskew_bbox(f.bbox, work)
            spans = [replace(s, bbox=unskew_bbox(s.bbox, work)) for s in spans]
        spans_by_page[original.index] = spans
        page.fields = fields
        pages.append(page)
    return Document(source, pages, spans_by_page)


def auto_straighten(page: PageImage) -> PageImage:
    """Measure the skew of a scanned page and straighten it (in place); keeps quarter turns."""
    return apply_orientation(page, Orientation(page.orientation.quarter_turns, auto_angle(page)))


def save_project(document: Document, path: Path) -> None:
    """Fields as JSON, so a review can be interrupted and resumed."""
    data = {
        "source": str(document.source),
        "replaces_source_fields": document.has_source_fields,
        "pages": [
            {
                "page": page.index,
                "quarter_turns": page.orientation.quarter_turns,
                "straighten_deg": page.orientation.straighten_deg,
                "fields": [
                    {
                        "name": f.name,
                        "type": f.type.value,
                        "bbox": [f.bbox.x0, f.bbox.y0, f.bbox.x1, f.bbox.y1],
                        "multiline": f.multiline,
                        "score": f.score,
                        **({"choice": f.choice} if f.choice else {}),
                    }
                    for f in page.fields
                ],
            }
            for page in document.pages
        ],
    }
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def load_project(path: Path, dpi: float = 150.0) -> Document:
    data = json.loads(path.read_text(encoding="utf-8"))
    source = Path(data["source"])
    by_page = {p["page"]: p for p in data["pages"]}
    pages = []
    for page in load_pages(source, dpi=dpi):
        entry = by_page.get(page.index, {})
        # Orientation first (fields are stored in the oriented view), then the fields.
        apply_orientation(
            page, Orientation(entry.get("quarter_turns", 0), entry.get("straighten_deg", 0.0))
        )
        page.fields = [
            Field(
                FieldType(f["type"]),
                BBox(*f["bbox"]),
                f.get("score", 1.0),
                f.get("multiline", False),
                f.get("name"),
                f.get("choice"),
            )
            for f in entry.get("fields", [])
        ]
        pages.append(page)
    return Document(source, pages, has_source_fields=data.get("replaces_source_fields", False))
