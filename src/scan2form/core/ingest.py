"""Load PDFs and images as rasterised pages."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps, ImageSequence

from scan2form.core.model import PT_PER_MM, PageImage

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DEFAULT_IMAGE_DPI = 300.0

# Standard paper sizes (short, long side in mm) used to recover the physical scale.
PAPER_SIZES_MM = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}
# Pages with a longer side than this (about 46 cm) are assumed to be
# scans stored at one point per pixel.
MAX_PLAUSIBLE_LONG_SIDE_PT = 1300.0


def physical_pt_per_mm(width: float, height: float, force: bool = False) -> float:
    """Physical scale of a page given in points (or pixels when `force`).

    Implausibly large pages are matched to the standard paper size with the
    closest aspect ratio (within 3%); otherwise the nominal 72/25.4 applies.
    """
    long_side, short_side = max(width, height), min(width, height)
    if not force and long_side <= MAX_PLAUSIBLE_LONG_SIDE_PT:
        return PT_PER_MM
    aspect = short_side / long_side
    short_mm, long_mm = min(
        PAPER_SIZES_MM.values(), key=lambda size: abs(size[0] / size[1] - aspect)
    )
    if abs(short_mm / long_mm - aspect) > 0.03:
        return PT_PER_MM
    return long_side / long_mm


def scan_info(page: pdfium.PdfPage) -> tuple[bool, float | None]:
    """(is a scan, pixels per point of the scan image).

    A scan has no text objects and one image covering most of the page. OCR'd scans
    (invisible text layer) count as digital: re-encoding them would drop the text.
    """
    if next(iter(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT])), None) is not None:
        return False, None
    width, height = page.get_size()
    best: tuple[float, float] | None = None
    for image in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = image.get_bounds()
        coverage = (right - left) * (top - bottom) / max(1.0, width * height)
        if coverage >= 0.8 and (best is None or coverage > best[0]):
            px_w, _ = image.get_px_size()
            best = (coverage, px_w / max(1.0, right - left))
    return (True, best[1]) if best else (False, None)


def count_pages(path: Path) -> int:
    """Number of pages (PDF) or frames (multi-page TIFF) without rendering anything."""
    if path.suffix.lower() in PDF_SUFFIXES:
        pdf = pdfium.PdfDocument(path)
        try:
            return len(pdf)
        finally:
            pdf.close()
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in PDF_SUFFIXES | IMAGE_SUFFIXES


def load_pages(path: Path, dpi: float = 150.0, draw_annots: bool = False) -> Iterator[PageImage]:
    """Yield pages of a PDF or image file.

    `draw_annots=False` hides existing form widgets, so a fillable PDF renders
    like its flat counterpart (useful for evaluating against its fields).
    `dpi` is the physical resolution: scans stored at one point per pixel are
    rendered at `dpi` relative to their real paper size, not their nominal one.
    Images keep their native resolution; their DPI metadata only sets the page size.
    """
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        yield from _load_pdf(path, dpi, draw_annots)
    elif suffix in IMAGE_SUFFIXES:
        yield from _load_image(path)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")


def _load_pdf(path: Path, dpi: float, draw_annots: bool) -> Iterator[PageImage]:
    pdf = pdfium.PdfDocument(path)
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            width_pt, height_pt = page.get_size()
            pt_per_mm = physical_pt_per_mm(width_pt, height_pt)
            scale = dpi / 25.4 / pt_per_mm
            bitmap = page.render(scale=scale, draw_annots=draw_annots)
            pixels = np.asarray(bitmap.to_pil().convert("RGB"))
            scanned, native = scan_info(page)
            yield PageImage(index, pixels, scale, width_pt, height_pt, pt_per_mm,
                            scanned=scanned, native_px_per_pt=native)  # fmt: skip
    finally:
        pdf.close()


def _load_image(path: Path) -> Iterator[PageImage]:
    with Image.open(path) as img:
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            frame = ImageOps.exif_transpose(frame).convert("RGB")
            pixels = np.asarray(frame)
            h, w = pixels.shape[:2]
            dpi_x = float((frame.info.get("dpi") or img.info.get("dpi") or (0,))[0])
            if dpi_x < 50:  # missing or bogus metadata: guess from the paper size
                px_per_mm = physical_pt_per_mm(w, h, force=True)
                dpi_x = px_per_mm * 25.4 if px_per_mm != PT_PER_MM else DEFAULT_IMAGE_DPI
            px_per_pt = dpi_x / 72.0
            yield PageImage(index, pixels, px_per_pt, w / px_per_pt, h / px_per_pt,
                            scanned=True, native_px_per_pt=px_per_pt)  # fmt: skip
