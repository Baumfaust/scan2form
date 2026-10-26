"""Read the fields of any fillable PDF and write a filled copy.

The source is never modified: filling always produces a new file.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject

from scan2form.core.autofill.mapping import FormField
from scan2form.core.ingest import physical_pt_per_mm
from scan2form.core.model import PT_PER_MM
from scan2form.core.pdfform import (
    FF_MULTILINE,
    FF_PUSHBUTTON,
    FF_RADIO,
    default_appearance,
    fit_font_size,
    inherited,
    on_states,
    qualified_name,
    text_font_size,
)

Source = Path | bytes


def _reader(source: Source) -> PdfReader:
    return PdfReader(io.BytesIO(source) if isinstance(source, bytes) else source)


def _kind(widget) -> str | None:
    ft = inherited(widget, "/FT")
    flags = int(inherited(widget, "/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Btn":
        if flags & FF_PUSHBUTTON:
            return None
        return "radio" if flags & FF_RADIO else "checkbox"
    if ft == "/Ch":
        return "choice"
    if ft == "/Sig":
        return "signature"
    return None


def _widgets(reader_or_writer):
    for page in reader_or_writer.pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                yield page, annot


def read_form_fields(source: Source) -> list[FormField]:
    """One FormField per fully qualified field; radio kids are merged into their group."""
    fields: dict[str, FormField] = {}
    for _page, widget in _widgets(_reader(source)):
        kind = _kind(widget)
        if kind is None:
            continue
        field_id = qualified_name(widget)
        tooltip = inherited(widget, "/TU")
        name = field_id.rsplit(".", 1)[-1]
        if kind == "radio":
            states = tuple(s.lstrip("/") for s in on_states(widget))
            previous = fields.get(field_id)
            choices = (previous.choices if previous else ()) + tuple(
                s for s in states if previous is None or s not in previous.choices
            )
        elif kind == "choice":
            options = inherited(widget, "/Opt") or []
            choices = tuple(str(o[1] if isinstance(o, list | ArrayObject) else o) for o in options)
        else:
            choices = ()
        fields[field_id] = FormField(
            field_id, name, str(tooltip) if tooltip else name, kind, choices
        )
    return list(fields.values())


_FONT_SIZE = re.compile(r"(/\S+)\s+([\d.]+)\s+Tf")


def _set_text_size(page, widget, value: str, acroform_da) -> None:
    """Give the widget a fixed font size: auto size (0 Tf) would make every box differ,
    and a long value shrinks just enough to fit instead of being clipped."""
    da = str(inherited(widget, "/DA") or acroform_da or "/Helv 0 Tf 0 g")
    m = _FONT_SIZE.search(da)
    x0, y0, x1, y1 = (float(v) for v in widget["/Rect"])
    width, height = abs(x1 - x0), abs(y1 - y0)
    mk = widget.get("/MK")
    if mk is not None and int(mk.get_object().get("/R", 0)) % 180:
        width, height = height, width
    box = page.mediabox
    scale = physical_pt_per_mm(float(box.width), float(box.height)) / PT_PER_MM
    size = float(m[2]) if m and float(m[2]) > 0 else text_font_size(height, scale)
    if not int(inherited(widget, "/Ff") or 0) & FF_MULTILINE:
        size = fit_font_size(value, size, width, scale)
    if m is None:
        new_da = f"{default_appearance(size)}"
    else:
        new_da = da[: m.start()] + f"{m[1]} {size:g} Tf" + da[m.end() :]
    widget[NameObject("/DA")] = TextStringObject(new_da)


def _terminal(widget):
    """The field dictionary holding /V: the widget itself, or its parent for kids without /T."""
    return widget if "/T" in widget else widget["/Parent"].get_object()


def fill_pdf(source: Source, values: dict[str, object], output: Path) -> int:
    """Write `values` (field id -> str | bool | radio state) into a copy; returns fields filled."""
    if isinstance(source, Path) and output.resolve() == source.resolve():
        raise ValueError("Refusing to overwrite the source; filling always writes a copy.")
    writer = PdfWriter(clone_from=_reader(source))
    filled: set[str] = set()
    texts: dict[str, str] = {}
    acroform = writer._root_object.get("/AcroForm")
    acroform_da = acroform.get_object().get("/DA") if acroform is not None else None
    for page, widget in _widgets(writer):
        field_id = qualified_name(widget)
        if field_id not in values:
            continue
        kind, value = _kind(widget), values[field_id]
        node = _terminal(widget)
        if kind == "checkbox":
            states = on_states(widget) or ["/Yes"]
            state = states[0] if value else "/Off"
            node[NameObject("/V")] = NameObject(state)
            widget[NameObject("/AS")] = NameObject(state)
        elif kind == "radio":
            state = f"/{value}" if value else "/Off"
            node[NameObject("/V")] = NameObject(state)
            widget[NameObject("/AS")] = NameObject(state if state in on_states(widget) else "/Off")
            if state not in on_states(widget):  # other kid of the group: stays off, keep looking
                continue
        elif kind in ("text", "choice"):
            texts[field_id] = str(value)
            if kind == "text":
                _set_text_size(page, widget, str(value), acroform_da)
        else:
            continue
        filled.add(field_id)
    if texts:
        # pypdf sets /V and builds appearance streams, so values show even in viewers
        # that ignore /NeedAppearances.
        writer.update_page_form_field_values(None, texts, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    with output.open("wb") as fh:
        writer.write(fh)
    return len(filled)


def default_output(source: Path) -> Path:
    stem = source.stem.removesuffix(".fillable")
    return source.with_name(f"{stem}.filled.pdf")


def read_form_values(source: Source) -> dict[str, str | bool]:
    """Values already filled in: text as str, ticked checkboxes as True, radio state names."""
    kinds = {f.id: f.kind for f in read_form_fields(source)}
    values: dict[str, str | bool] = {}
    for name, data in (_reader(source).get_fields() or {}).items():
        kind, raw = kinds.get(name), data.get("/V")
        if kind is None or raw is None:
            continue
        text = str(raw)
        if kind == "checkbox":
            if text not in ("/Off", ""):
                values[name] = True
        elif kind == "radio":
            if text not in ("/Off", ""):
                values[name] = text.lstrip("/")
        elif text.strip():
            values[name] = text
    return values
