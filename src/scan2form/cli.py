"""Headless conversion: scan2form input.pdf -o output.pdf"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scan2form.core.detect import OpenCVDetector
from scan2form.core.export import export_pdf
from scan2form.core.ingest import is_supported
from scan2form.core.ocr import Ocr
from scan2form.core.pipeline import process, save_project


def build_detector(name: str, confidence: float):
    if name == "opencv":
        return OpenCVDetector()
    if name in ("ffdnet-s", "ffdnet-l"):
        from scan2form.core.detect.ffdnet_detector import FFDNetDetector

        return FFDNetDetector("FFDNet-" + name[-1].upper(), confidence=confidence)
    raise SystemExit(f"Unknown detector: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan2form", description="Turn a scanned or flat form into a fillable PDF."
    )
    parser.add_argument("input", type=Path, help="PDF or image (PNG, JPG, TIFF)")
    parser.add_argument("-o", "--output", type=Path, help="default: <input>.fillable.pdf")
    parser.add_argument(
        "-d", "--detector", default="opencv", choices=["opencv", "ffdnet-s", "ffdnet-l"]
    )
    parser.add_argument("--confidence", type=float, default=0.15, help="FFDNet score threshold")
    parser.add_argument("--no-ocr", action="store_true", help="skip OCR; fields get generic names")
    parser.add_argument("--dpi", type=float, default=150.0, help="processing resolution")
    parser.add_argument("--project", type=Path, help="also save detected fields as project JSON")
    args = parser.parse_args(argv)

    if not args.input.exists() or not is_supported(args.input):
        print(f"Not a supported input file: {args.input}", file=sys.stderr)
        return 2
    output = args.output or args.input.with_name(args.input.stem + ".fillable.pdf")

    def progress(index: int, page_count: int, step: str) -> None:
        print(f"  page {index + 1}/{page_count}: {step}", file=sys.stderr)

    document = process(
        args.input,
        build_detector(args.detector, args.confidence),
        None if args.no_ocr else Ocr(),
        dpi=args.dpi,
        progress=progress,
    )
    if args.project:
        save_project(document, args.project)
    count = export_pdf(args.input, document.pages, output)
    print(f"{count} fields on {len(document.pages)} page(s) -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
