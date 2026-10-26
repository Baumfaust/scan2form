"""Compare field detectors on a folder of forms.

    uv run scan2form-spike local_tests/fixtures
    uv run --group ffdnet-eval scan2form-spike local_tests/fixtures -d opencv,ffdnet-l

Ground truth comes from existing AcroForm widgets (fillable PDFs, rendered with
widgets hidden) or a `<file>.gt.json` sidecar. Files without ground truth are
still processed and get overlays and timings, just no scores.

Output defaults to `<input-dir>/spike-out/`, so results derived from private
forms stay next to them (local_tests/ is gitignored and must never leave the PC).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2

from scan2form.core.detect import Detector, OpenCVDetector
from scan2form.core.evaluate import Score, match
from scan2form.core.groundtruth import load_ground_truth
from scan2form.core.ingest import is_supported, load_pages
from scan2form.core.model import Field, FieldType, PageImage
from scan2form.core.preprocess import deskew

# BGR colours for overlays
GT_COLOR = (0, 170, 0)
TYPE_COLORS = {
    FieldType.TEXT: (220, 80, 0),
    FieldType.CHECKBOX: (0, 0, 220),
    FieldType.SIGNATURE: (180, 0, 180),
}


def build_detectors(names: list[str], confidence: float) -> list[Detector]:
    detectors: list[Detector] = []
    for name in names:
        if name == "opencv":
            detectors.append(OpenCVDetector())
        elif name in ("ffdnet-s", "ffdnet-l"):
            from scan2form.core.detect.ffdnet_detector import FFDNetDetector

            detectors.append(FFDNetDetector("FFDNet-" + name[-1].upper(), confidence=confidence))
        else:
            raise SystemExit(f"Unknown detector: {name}")
    return detectors


def _markup_sources(directory: Path) -> set[Path]:
    """Screenshots used to create a ground-truth sidecar (see import_markup)."""
    sources = set()
    for sidecar in directory.rglob("*.gt.json"):
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        for page in data.get("pages", []):
            sources.update(sidecar.parent / name for name in page.get("sources", []))
    return sources


def collect_inputs(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            skip = _markup_sources(path)
            files.extend(
                sorted(
                    p
                    for p in path.rglob("*")
                    if is_supported(p) and "spike-out" not in p.parts and p not in skip
                )
            )
        elif is_supported(path):
            files.append(path)
    return files


def draw_overlay(
    page: PageImage, gt: list[Field] | None, predicted: list[Field], path: Path
) -> None:
    image = cv2.cvtColor(page.pixels, cv2.COLOR_RGB2BGR)
    s = page.px_per_pt

    def rect(field: Field, color, thickness):
        b = field.bbox.scaled(s)
        cv2.rectangle(image, (int(b.x0), int(b.y0)), (int(b.x1), int(b.y1)), color, thickness)

    for field in gt or []:
        rect(field, GT_COLOR, 3)
    for field in predicted:
        rect(field, TYPE_COLORS[field.type], 1)
    cv2.imwrite(str(path), image)


def field_json(field: Field) -> dict:
    data = asdict(field)
    data["type"] = field.type.value
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "-d", "--detectors", default="opencv", help="comma list: opencv,ffdnet-s,ffdnet-l"
    )
    parser.add_argument("--dpi", type=float, default=150.0, help="PDF render resolution")
    parser.add_argument(
        "--iou", type=float, default=0.5, help="match threshold (IoU, or coverage with --loose)"
    )
    parser.add_argument(
        "--loose", action="store_true", help="match by row + horizontal coverage (hand-marked GT)"
    )
    parser.add_argument("--confidence", type=float, default=0.3, help="FFDNet score threshold")
    parser.add_argument(
        "-o", "--out", type=Path, help="output dir (default: <first input>/spike-out)"
    )
    parser.add_argument("--no-deskew", action="store_true")
    args = parser.parse_args(argv)

    files = collect_inputs(args.inputs)
    if not files:
        print("No supported input files found.", file=sys.stderr)
        return 1
    first = args.inputs[0]
    out_dir = args.out or (first if first.is_dir() else first.parent) / "spike-out"
    out_dir.mkdir(parents=True, exist_ok=True)

    detectors = build_detectors(
        [n.strip() for n in args.detectors.split(",") if n.strip()], args.confidence
    )
    totals = {d.name: Score() for d in detectors}
    timings = {d.name: [] for d in detectors}
    rows = []

    for file in files:
        gt_pages = load_ground_truth(file)
        file_out = out_dir / file.stem
        file_out.mkdir(exist_ok=True)
        for page in load_pages(file, dpi=args.dpi, draw_annots=False):
            # Ground truth is in original page coordinates, so never rotate those pages.
            if gt_pages is None and not args.no_deskew:
                page = deskew(page)
            gt = gt_pages.get(page.index, []) if gt_pages is not None else None
            for detector in detectors:
                start = time.perf_counter()
                predicted = detector.detect(page)
                elapsed_ms = (time.perf_counter() - start) * 1000
                timings[detector.name].append(elapsed_ms)

                loose_dy = 4.0 * page.pt_per_mm if args.loose else None
                score = match(predicted, gt, args.iou, loose_dy) if gt is not None else None
                if score is not None:
                    totals[detector.name] += score
                rows.append(
                    {
                        "file": file.name,
                        "page": page.index + 1,
                        "detector": detector.name,
                        "skew_deg": round(page.skew_deg, 2),
                        "ms": round(elapsed_ms, 1),
                        "predicted": len(predicted),
                        "expected": "" if gt is None else len(gt),
                        "precision": "" if score is None else round(score.precision, 3),
                        "recall": "" if score is None else round(score.recall, 3),
                        "f1": "" if score is None else round(score.f1, 3),
                        "type_acc": "" if score is None else round(score.type_accuracy, 3),
                    }
                )
                stem = f"page-{page.index + 1:02d}-{detector.name}"
                draw_overlay(page, gt, predicted, file_out / f"{stem}.png")
                (file_out / f"{stem}.json").write_text(
                    json.dumps([field_json(f) for f in predicted], indent=1), encoding="utf-8"
                )

    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(files)} file(s), {len(rows) // len(detectors)} page(s) -> {out_dir}")
    print(f"{'detector':<10} {'P':>6} {'R':>6} {'F1':>6} {'type':>6} {'ms/page':>8}")
    for detector in detectors:
        s = totals[detector.name]
        ms = sum(timings[detector.name]) / len(timings[detector.name])
        if s.expected:
            print(
                f"{detector.name:<10} {s.precision:6.2f} {s.recall:6.2f} {s.f1:6.2f} "
                f"{s.type_accuracy:6.2f} {ms:8.0f}"
            )
        else:
            print(f"{detector.name:<10} {'(no ground truth)':>27} {ms:8.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
