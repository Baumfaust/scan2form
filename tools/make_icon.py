"""Generate the Scan2Form app icon (PNG sizes + Windows .ico).

    uv run python tools/make_icon.py

Motif: a form page on a blue tile with text fields, a ticked checkbox and a
scan beam. Drawn on a 256-unit grid at 4x supersampling; sizes <= 32 px use a
simplified variant so they stay legible. Colours match the field colours in
the review UI (ui/page_view.py TYPE_COLORS).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "scan2form" / "ui" / "assets"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256, 512]
SUPERSAMPLE = 4

TILE_TOP, TILE_BOTTOM = (37, 99, 235), (30, 58, 160)
PAGE, PAGE_FOLD, SHADOW = (255, 255, 255), (203, 213, 225), (10, 20, 60, 90)
FIELD = (30, 110, 220)
LABEL = (148, 163, 184)
CHECK = (20, 150, 60)
BEAM = (245, 158, 11)


def _gradient(size: int) -> Image.Image:
    grad = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TILE_TOP, TILE_BOTTOM)))
    return grad.resize((size, size))


def draw(px: int, simple: bool) -> Image.Image:
    size = px * SUPERSAMPLE
    k = size / 256  # design grid -> pixels

    def r(*v):
        return [round(x * k) for x in v]

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Tile: rounded square with a vertical gradient.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(r(8, 8, 248, 248), radius=round(52 * k), fill=255)
    img.paste(_gradient(size), (0, 0), mask)

    # Page with soft shadow and folded corner (top right).
    page_box, fold = (60, 34, 196, 222), 34
    x0, y0, x1, y1 = page_box
    outline = [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)]
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon(
        [(round((x + 4) * k), round((y + 7) * k)) for x, y in outline], fill=SHADOW
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6 * k)))
    d = ImageDraw.Draw(img)
    d.polygon([(round(x * k), round(y * k)) for x, y in outline], fill=PAGE)
    d.polygon(
        [
            (round(x * k), round(y * k))
            for x, y in [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)]
        ],
        fill=PAGE_FOLD,
    )

    if simple:
        # Two bold fields and a big ticked checkbox: readable at 16-32 px.
        d.rounded_rectangle(
            r(80, 72, 176, 98), radius=round(5 * k), outline=FIELD, width=round(12 * k)
        )
        d.rounded_rectangle(
            r(80, 118, 176, 144), radius=round(5 * k), outline=FIELD, width=round(12 * k)
        )
        d.rectangle(r(80, 164, 120, 204), outline=CHECK, width=round(12 * k))
        d.line(r(88, 184, 98, 196, 116, 166), fill=CHECK, width=round(12 * k), joint="curve")
        return img.resize((px, px), Image.Resampling.LANCZOS)

    # Rows: grey label + blue text field.
    for y in (66, 104):
        d.rounded_rectangle(r(78, y + 8, 104, y + 16), radius=round(3 * k), fill=LABEL)
        d.rounded_rectangle(
            r(112, y, 178, y + 24), radius=round(4 * k), outline=FIELD, width=round(5 * k)
        )
    # Checkbox row: ticked box + label.
    d.rounded_rectangle(
        r(78, 146, 102, 170), radius=round(3 * k), outline=CHECK, width=round(5 * k)
    )
    d.line(r(83, 158, 89, 165, 100, 148), fill=CHECK, width=round(6 * k), joint="curve")
    d.rounded_rectangle(r(112, 154, 160, 162), radius=round(3 * k), fill=LABEL)
    # Signature line, below the scan beam.
    d.line(r(112, 207, 178, 207), fill=FIELD, width=round(4 * k))

    # Scan beam: a glowing bar across the page, overhanging the edges.
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle(r(40, 180, 216, 192), fill=(*BEAM, 150))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(7 * k)))
    d.rounded_rectangle(r(42, 184, 214, 189), radius=round(3 * k), fill=BEAM)
    return img.resize((px, px), Image.Resampling.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    images = {px: draw(px, simple=px <= 32) for px in SIZES}
    for px, image in images.items():
        image.save(OUT_DIR / f"icon-{px}.png")
    # .ico for Windows (taskbar, Explorer, installer); Pillow picks the frames by size.
    images[256].save(
        OUT_DIR / "icon.ico",
        sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
        append_images=[images[s] for s in (16, 20, 24, 32, 40, 48, 64, 128)],
    )
    print(f"wrote {len(images)} PNGs + icon.ico to {OUT_DIR}")


if __name__ == "__main__":
    main()
