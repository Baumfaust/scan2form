"""Write detected fields into a fillable PDF (AcroForm).

PDF input: pages are kept untouched and widgets are added on top, except for
re-oriented pages: scanned pages are replaced by the straightened scan image
(rendered at the scan's native resolution), digital pages only get lossless
quarter turns via /Rotate. Image input: each image becomes a PDF page first.
"""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from scan2form.core.coords import PageGeometry
from scan2form.core.ingest import IMAGE_SUFFIXES
from scan2form.core.model import PT_PER_MM, Field, FieldType, PageImage
from scan2form.core.pdfform import FF_RADIO, default_appearance, text_font_size
from scan2form.core.straighten import render_view

_FF_MULTILINE = 1 << 12
_ANNOT_PRINT = 1 << 2
_DEFAULT_APPEARANCE = "/Helv 0 Tf 0 g"  # auto font size, black


_FF_NO_TOGGLE_TO_OFF = 1 << 14


def export_pdf(
    source: Path, pages: list[PageImage], output: Path, replace_existing: bool = False
) -> int:
    """Write `pages[i].fields` (view coordinates) onto `source`; return the field count.

    `replace_existing` drops the source's own form fields first (a fillable PDF whose
    fields were loaded and edited), so they are not duplicated.
    """
    writer = PdfWriter(clone_from=_open_source(source, pages))
    if replace_existing:
        _remove_form_fields(writer)
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        _apply_orientations(writer, source, pages)
    acroform = _ensure_acroform(writer)
    fields_array = acroform["/Fields"]
    # A name used by several widgets (radio buttons, or a field repeated on several pages)
    # becomes one field with the widgets as kids, so they share one value.
    uses = Counter(f.name or "field" for p in pages for f in p.fields)
    groups: dict[str, DictionaryObject] = {}
    count = 0
    for page_image in pages:
        pdf_page = writer.pages[page_image.index]
        geometry = PageGeometry.from_pypdf(pdf_page)
        if "/Annots" not in pdf_page:
            pdf_page[NameObject("/Annots")] = ArrayObject()
        annots = pdf_page["/Annots"]
        for field in page_image.fields:
            widget = _widget(writer, field, geometry, page_image.pt_per_mm / PT_PER_MM)
            widget[NameObject("/P")] = pdf_page.indirect_reference
            ref = writer._add_object(widget)
            annots.append(ref)
            name = field.name or "field"
            radio = field.choice is not None and field.type is FieldType.CHECKBOX
            if not radio and uses[name] == 1:
                fields_array.append(ref)
                count += 1
                continue
            group = groups.get(name)
            if group is None:
                group = DictionaryObject({
                    NameObject("/T"): TextStringObject(name),
                    NameObject("/Kids"): ArrayObject(),
                })  # fmt: skip
                for key in ("/FT", "/V", "/Ff"):  # field-level attributes move to the parent
                    if key in widget:
                        group[NameObject(key)] = widget[key]
                if radio:
                    group[NameObject("/Ff")] = NumberObject(FF_RADIO | _FF_NO_TOGGLE_TO_OFF)
                    group[NameObject("/V")] = NameObject("/Off")
                groups[name] = group
                fields_array.append(writer._add_object(group))
                count += 1
            for key in ("/T", "/FT", "/V", "/Ff"):
                if key in widget:
                    del widget[key]
            widget[NameObject("/Parent")] = group.indirect_reference
            group["/Kids"].append(ref)
    with output.open("wb") as fh:
        writer.write(fh)
    return count


def _open_source(source: Path, pages: list[PageImage]) -> PdfReader:
    if source.suffix.lower() in IMAGE_SUFFIXES:
        return PdfReader(io.BytesIO(_images_to_pdf(source, pages)))
    return PdfReader(source)


MAX_EXPORT_DPI = 400.0  # physical resolution cap for re-encoded scans
JPEG_QUALITY = 90


def _encode_page(pixels: np.ndarray, px_per_pt: float) -> bytes:
    """A one-page PDF showing `pixels` at `px_per_pt` (so its size in points matches the view)."""
    image = Image.fromarray(pixels)
    channel_spread = np.ptp(pixels[::8, ::8].astype(np.int16), axis=2) if pixels.ndim == 3 else None
    if channel_spread is not None and int(channel_spread.max()) < 12:
        image = image.convert("L")  # grey scan: a third of the size, no visible difference
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=px_per_pt * 72.0, quality=JPEG_QUALITY)
    return buffer.getvalue()


def _apply_orientations(writer: PdfWriter, source: Path, pages: list[PageImage]) -> None:
    pdf = None
    try:
        for page in pages:
            o = page.orientation
            if o.is_identity:
                continue
            if not page.scanned:
                writer.pages[page.index].rotate(90 * o.quarter_turns)  # lossless; fine angle n/a
                continue
            if pdf is None:
                pdf = pdfium.PdfDocument(source)
            native = page.native_px_per_pt or page.px_per_pt
            scale = min(native, MAX_EXPORT_DPI / 25.4 / page.pt_per_mm)
            base = np.asarray(
                pdf[page.index].render(scale=scale, draw_annots=False).to_pil().convert("RGB")
            )
            straight = PdfReader(io.BytesIO(_encode_page(render_view(base, o), scale))).pages[0]
            writer.insert_page(straight, page.index)
            writer.remove_page(page.index + 1)
    finally:
        if pdf is not None:
            pdf.close()


def _images_to_pdf(source: Path, pages: list[PageImage]) -> bytes:
    """One PDF page per image frame in its view orientation, sized so view points match `pages`."""
    with Image.open(source) as img:
        frames = [ImageOps.exif_transpose(f).convert("RGB") for f in ImageSequence.Iterator(img)]
    by_index = {p.index: p for p in pages}
    writer = PdfWriter()
    for index, frame in enumerate(frames):
        page = by_index.get(index)
        pixels = np.asarray(frame)
        px_per_pt = page.px_per_pt if page else 300.0 / 72.0
        if page is not None and not page.orientation.is_identity:
            pixels = render_view(pixels, page.orientation)
        writer.add_page(PdfReader(io.BytesIO(_encode_page(pixels, px_per_pt))).pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _ensure_acroform(writer: PdfWriter) -> DictionaryObject:
    root = writer._root_object
    if "/AcroForm" not in root:
        root[NameObject("/AcroForm")] = writer._add_object(DictionaryObject())
    acroform = root["/AcroForm"].get_object()
    acroform.setdefault(NameObject("/Fields"), ArrayObject())
    acroform[NameObject("/NeedAppearances")] = BooleanObject(True)
    acroform.setdefault(NameObject("/DA"), TextStringObject(_DEFAULT_APPEARANCE))
    resources = acroform.setdefault(NameObject("/DR"), DictionaryObject())
    fonts = resources.setdefault(NameObject("/Font"), DictionaryObject())
    if "/Helv" not in fonts:
        fonts[NameObject("/Helv")] = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }
            )
        )
    return acroform


def _widget(
    writer: PdfWriter, field: Field, geometry: PageGeometry, page_scale: float
) -> DictionaryObject:
    x0, y0, x1, y1 = geometry.view_bbox_to_pdf_rect(field.bbox)
    widget = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([FloatObject(round(v, 2)) for v in (x0, y0, x1, y1)]),
            NameObject("/F"): NumberObject(_ANNOT_PRINT),
            NameObject("/T"): TextStringObject(field.name or "field"),
        }
    )
    if field.type is FieldType.CHECKBOX:
        widget[NameObject("/FT")] = NameObject("/Btn")
        widget[NameObject("/V")] = NameObject("/Off")
        widget[NameObject("/AS")] = NameObject("/Off")
        state = _state_name(field.choice) if field.choice is not None else "/Yes"
        widget[NameObject("/AP")] = _checkbox_appearance(writer, x1 - x0, y1 - y0, state)
    elif field.type is FieldType.SIGNATURE:
        widget[NameObject("/FT")] = NameObject("/Sig")
    else:
        widget[NameObject("/FT")] = NameObject("/Tx")
        # A fixed size, the same on every field: auto size (0 Tf) makes each box differ.
        size = text_font_size(field.bbox.height, page_scale)
        widget[NameObject("/DA")] = TextStringObject(default_appearance(size))
        if field.multiline:
            widget[NameObject("/Ff")] = NumberObject(_FF_MULTILINE)
    if geometry.rotate:
        # Keep text upright for the reader on rotated pages.
        widget[NameObject("/MK")] = DictionaryObject(
            {NameObject("/R"): NumberObject(geometry.rotate)}
        )
    return widget


def _state_name(choice: str) -> str:
    """PDF name for a radio state; names cannot hold spaces or delimiters."""
    safe = "".join(c if c.isalnum() or c in "_-." else "_" for c in choice)
    return "/" + (safe or "Choice")


def _remove_form_fields(writer: PdfWriter) -> None:
    for page in writer.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        kept = ArrayObject(
            a for a in annots.get_object() if a.get_object().get("/Subtype") != "/Widget"
        )
        page[NameObject("/Annots")] = kept
    root = writer._root_object
    if "/AcroForm" in root:
        acroform = root["/AcroForm"].get_object()
        acroform[NameObject("/Fields")] = ArrayObject()
        for key in ("/XFA", "/CO"):  # XFA would override the AcroForm in some viewers
            if key in acroform:
                del acroform[key]


def _checkbox_appearance(
    writer: PdfWriter, width: float, height: float, state: str = "/Yes"
) -> DictionaryObject:
    """/Yes draws a check mark as a path (no font needed), /Off draws nothing."""
    w, h = width, height
    on = (
        f"q 0 g 0 G {max(0.8, min(w, h) / 10):.2f} w 1 J 1 j "
        f"{0.2 * w:.2f} {0.5 * h:.2f} m {0.42 * w:.2f} {0.25 * h:.2f} l "
        f"{0.8 * w:.2f} {0.78 * h:.2f} l S Q"
    )

    def stream(content: str):
        obj = DecodedStreamObject()
        obj.set_data(content.encode("ascii"))
        obj.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject(
                    [FloatObject(0), FloatObject(0), FloatObject(w), FloatObject(h)]
                ),
            }
        )
        return writer._add_object(obj)

    return DictionaryObject(
        {
            NameObject("/N"): DictionaryObject(
                {NameObject(state): stream(on), NameObject("/Off"): stream("")}
            )
        }
    )
