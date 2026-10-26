"""Small helpers for reading AcroForm structures with pypdf."""

from __future__ import annotations

from pypdf.generic import DictionaryObject

FF_MULTILINE = 1 << 12
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
ANNOT_HIDDEN = 1 << 1


def inherited(node: DictionaryObject, key: str):
    """Field attribute, looked up through the /Parent chain (PDF fields inherit /FT, /Ff, …)."""
    while node is not None:
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return None


def qualified_name(node: DictionaryObject) -> str:
    """Fully qualified field name ("parent.child"), as pypdf's get_fields() reports it."""
    parts = []
    while node is not None:
        if "/T" in node:
            parts.append(str(node["/T"]))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))


def on_states(widget: DictionaryObject) -> list[str]:
    """Appearance states of a checkbox/radio widget other than /Off, e.g. ["/Yes"]."""
    appearance = widget.get("/AP")
    normal = appearance.get_object().get("/N") if appearance is not None else None
    if normal is None or not hasattr(normal.get_object(), "keys"):
        return []
    return [str(k) for k in normal.get_object() if str(k) != "/Off"]


# -- text appearance -------------------------------------------------------------------
TEXT_SIZE_PT = 10.0  # physical font size of filled-in text (on paper)
_MAX_SIZE_PER_HEIGHT = 0.7  # single-line text must fit the box height
_AVG_CHAR_EM = 0.55  # Helvetica, mixed text; close enough to decide when to shrink


def text_font_size(height: float, page_scale: float = 1.0) -> float:
    """One size for all fields of a page (in page units), smaller only for boxes too low.

    `page_scale` is page units per physical point: 1 for normal PDFs, more for scans
    stored at one point per pixel (see ingest.physical_pt_per_mm).
    """
    return round(min(TEXT_SIZE_PT * page_scale, height * _MAX_SIZE_PER_HEIGHT), 1)


def fit_font_size(text: str, size: float, width: float, page_scale: float = 1.0) -> float:
    """Shrink `size` only as far as needed for `text` to fit on one line of `width`."""
    usable = width - 4 * page_scale  # pypdf/Acrobat pad text by ~2 pt each side
    needed = len(text) * _AVG_CHAR_EM * size
    return size if needed <= usable or not text else round(max(size * usable / needed, size / 3), 1)


def default_appearance(size: float) -> str:
    return f"/Helv {size:g} Tf 0 g"
