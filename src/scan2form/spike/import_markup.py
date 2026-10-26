"""Turn hand-marked screenshots into a ground-truth sidecar.

    uv run python -m scan2form.spike.import_markup local_tests/form.pdf \
        local_tests/marks-page*.png

Each screenshot shows one page of the PDF with fields marked in red. Every
mark is refined with core.refine.refine_mark, the same logic the review UI
uses for rough user marks (snap to printed checkbox, box or line).

Screenshots may be cropped or zoomed: each one is registered to the best
matching page with ORB features + a similarity transform. Red that already
exists in the original page is ignored. Everything runs locally.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from scan2form.core.groundtruth import sidecar_path
from scan2form.core.ingest import load_pages
from scan2form.core.model import BBox, PageImage
from scan2form.core.refine import STROKE_MAX_HEIGHT_MM, binarize, refine_mark

REGISTRATION_DPI = 120.0
MIN_INLIERS = 25
MAX_STROKE_GAP_MM = 1.5


def red_mask(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = cv2.split(hsv)
    return (((h <= 10) | (h >= 170)) & (s >= 120) & (v >= 90)).astype(np.uint8) * 255


def _gray_without_red(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray[cv2.dilate(red_mask(rgb), np.ones((5, 5), np.uint8)) > 0] = 255
    return gray


def register(shot_gray: np.ndarray, page_gray: np.ndarray) -> tuple[np.ndarray | None, int]:
    """Similarity transform screenshot -> page pixels, and its RANSAC inlier count."""
    orb = cv2.ORB_create(nfeatures=6000)
    kp1, des1 = orb.detectAndCompute(shot_gray, None)
    kp2, des2 = orb.detectAndCompute(page_gray, None)
    if des1 is None or des2 is None:
        return None, 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des1, des2, k=2)
    good = [
        m for m, n in (pair for pair in matches if len(pair) == 2) if m.distance < 0.75 * n.distance
    ]
    if len(good) < MIN_INLIERS:
        return None, len(good)
    src = np.float32([kp1[m.queryIdx].pt for m in good])
    dst = np.float32([kp2[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0
    )
    return matrix, int(inliers.sum()) if inliers is not None else 0


def merge_stroke_pieces(components: list[tuple], px_per_mm: float) -> list[tuple]:
    """Join pieces of one hand-drawn underline (same row, gap <= MAX_STROKE_GAP_MM).

    Components are (x, y, w, h, area) in pixels. A wobbling pencil stroke breaks
    into touching pieces; separate fields on one row are normally further apart.
    """
    max_gap = MAX_STROKE_GAP_MM * px_per_mm
    max_dy = 2.0 * px_per_mm
    thin = STROKE_MAX_HEIGHT_MM * px_per_mm
    strokes = sorted((c for c in components if c[3] < thin), key=lambda c: (c[1] + c[3] / 2, c[0]))
    others = [c for c in components if c[3] >= thin]
    merged = True
    while merged:
        merged = False
        strokes.sort(key=lambda c: c[0])
        result: list[tuple] = []
        for c in strokes:
            for i, r in enumerate(result):
                same_row = abs((c[1] + c[3] / 2) - (r[1] + r[3] / 2)) <= max_dy
                gap = max(c[0] - (r[0] + r[2]), r[0] - (c[0] + c[2]))
                if same_row and gap <= max_gap:
                    x0, y0 = min(r[0], c[0]), min(r[1], c[1])
                    x1, y1 = max(r[0] + r[2], c[0] + c[2]), max(r[1] + r[3], c[1] + c[3])
                    result[i] = (x0, y0, x1 - x0, y1 - y0, r[4] + c[4])
                    merged = True
                    break
            else:
                result.append(c)
        strokes = result
    return strokes + others


def marks_to_fields(
    shot_rgb: np.ndarray, matrix: np.ndarray, page: PageImage
) -> tuple[list[dict], dict]:
    px_per_mm = page.px_per_mm
    page_red = cv2.dilate(red_mask(page.pixels), np.ones((7, 7), np.uint8))
    binary = binarize(page)

    # Warp the red marks into page pixel space, then drop red the page already had.
    h, w = binary.shape
    marks = cv2.warpAffine(red_mask(shot_rgb), matrix, (w, h), flags=cv2.INTER_NEAREST)
    marks[page_red > 0] = 0
    marks = cv2.morphologyEx(marks, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(marks)
    components = merge_stroke_pieces([tuple(int(v) for v in s) for s in stats[1:]], px_per_mm)

    fields: list[dict] = []
    stats_out = {"checkbox": 0, "text": 0, "snapped": 0, "ignored": 0}
    to_pt = 1.0 / page.px_per_pt
    for x, y, bw, bh, area in components:
        if area < 0.5 * px_per_mm**2:
            stats_out["ignored"] += 1  # specks
            continue
        mark = BBox(x * to_pt, y * to_pt, (x + bw) * to_pt, (y + bh) * to_pt)
        field = refine_mark(page, mark, binary)
        stats_out[field.type.value] += 1
        stats_out["snapped"] += field.score == 1.0
        fields.append(
            {
                "type": field.type.value,
                "bbox": [
                    round(v, 2)
                    for v in (field.bbox.x0, field.bbox.y0, field.bbox.x1, field.bbox.y1)
                ],
                "multiline": field.multiline,
                "source": "markup",
            }
        )
    return fields, stats_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("document", type=Path)
    parser.add_argument("screenshots", nargs="+", type=Path)
    args = parser.parse_args(argv)

    pages = list(load_pages(args.document, dpi=REGISTRATION_DPI))
    page_grays = [_gray_without_red(p.pixels) for p in pages]
    result_pages = []
    for shot_path in args.screenshots:
        shot_rgb = np.asarray(Image.open(shot_path).convert("RGB"))
        shot_gray = _gray_without_red(shot_rgb)
        candidates = [(register(shot_gray, g), i) for i, g in enumerate(page_grays)]
        (matrix, inliers), index = max(candidates, key=lambda c: c[0][1])
        if matrix is None or inliers < MIN_INLIERS:
            print(f"{shot_path.name}: could not register (best {inliers} inliers)", file=sys.stderr)
            return 1
        scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
        rotation = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
        fields, stats = marks_to_fields(shot_rgb, matrix, pages[index])
        print(
            f"{shot_path.name}: page {index + 1}, inliers {inliers}, scale {scale:.3f}, "
            f"rot {rotation:+.2f} deg, fields {len(fields)} {stats}"
        )
        result_pages.append({"page": index, "source": shot_path.name, "fields": fields})

    if len({p["page"] for p in result_pages}) != len(result_pages):
        print(
            "Warning: several screenshots matched the same page; their fields are merged.",
            file=sys.stderr,
        )
    merged: dict[int, dict] = {}
    for p in result_pages:
        entry = merged.setdefault(p["page"], {"page": p["page"], "sources": [], "fields": []})
        entry["sources"].append(p["source"])
        entry["fields"].extend(p["fields"])
    out = sidecar_path(args.document)
    out.write_text(
        json.dumps({"pages": sorted(merged.values(), key=lambda p: p["page"])}, indent=1),
        encoding="utf-8",
    )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
