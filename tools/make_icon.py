"""Generates the integration's brand icon/logo from the Avant Tecnologia
source artwork, following the Home Assistant brand image spec
(https://github.com/home-assistant/brands and the local-brand-images
support added in HA 2026.3 -
https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).

Run from the repo root:

    python tools/make_icon.py <path-to-source-png>

The source is expected to be the Avant Tecnologia logo lockup on a plain
white background (glyph mark on top, "avant" wordmark, "TECNOLOGIA"
tagline). This script:

  1. Removes the white background (unmatte against white, so anti-aliased
     edges stay smooth instead of leaving a white fringe).
  2. Crops the glyph mark alone (square-ish) for icon.png / icon@2x.png.
  3. Crops the glyph + "avant" wordmark for logo.png / logo@2x.png.
  4. Produces dark_logo.png / dark_logo@2x.png with the wordmark recolored
     to white, since black text on Home Assistant's dark theme would be
     unreadable. The glyph mark itself needs no dark variant since it has
     no black elements.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aat_multiroom" / "brand"

# Row ranges within the *source* image (pixel coordinates), found by
# inspecting the row-content profile of the Avant lockup: the glyph mark,
# then a gap, then the "avant" wordmark, then a gap, then "TECNOLOGIA".
GLYPH_ROWS = (36, 179)
WORDMARK_ROWS = (36, 281)  # glyph + "avant" (excludes the smaller tagline)
WORDMARK_TEXT_START_ROW = 189  # where "avant" itself starts (glyph ends at 179)


def unmatte_white(img: Image.Image) -> Image.Image:
    """Remove a solid white background, keeping smooth anti-aliased edges,
    via the standard 'difference matte against a known background' trick:
    alpha is derived from how far a pixel is from white, then the
    foreground color is un-blended from that alpha."""
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    min_channel = arr.min(axis=2, keepdims=True)
    alpha = np.clip(255.0 - min_channel, 0, 255)
    with np.errstate(divide="ignore", invalid="ignore"):
        fg = (arr - (255.0 - alpha)) / np.clip(alpha / 255.0, 1e-6, None)
    fg = np.clip(fg, 0, 255)
    rgba = np.concatenate([fg, alpha], axis=2).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def crop_content(img: Image.Image, y0: int, y1: int) -> Image.Image:
    """Crop to a row range, then trim to the actual non-transparent bbox
    within it (minimal empty space, per the brand image spec)."""
    band = img.crop((0, y0, img.width, y1 + 1))
    bbox = band.getbbox()
    return band.crop(bbox) if bbox else band


def pad_to_square(img: Image.Image, margin_ratio: float = 0.08) -> Image.Image:
    side = max(img.width, img.height)
    side = round(side * (1 + margin_ratio * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def recolor_wordmark_to_white(logo: Image.Image, text_top_ratio: float) -> Image.Image:
    """logo is the glyph+'avant' crop; recolor only the wordmark portion
    (below text_top_ratio of the height) to white, preserving alpha."""
    arr = np.array(logo)
    split_row = round(logo.height * text_top_ratio)
    arr[split_row:, :, 0] = 255
    arr[split_row:, :, 1] = 255
    arr[split_row:, :, 2] = 255
    return Image.fromarray(arr, mode="RGBA")


def resize_height(img: Image.Image, height: int) -> Image.Image:
    width = round(img.width * (height / img.height))
    return img.resize((width, height), Image.LANCZOS)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python tools/make_icon.py <path-to-source-logo.png>")
        raise SystemExit(1)

    source = Image.open(sys.argv[1])
    cutout = unmatte_white(source)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- icon.png / icon@2x.png: glyph mark only, padded to a square ---
    glyph = crop_content(cutout, *GLYPH_ROWS)
    glyph_square = pad_to_square(glyph)
    icon_512 = glyph_square.resize((512, 512), Image.LANCZOS)
    icon_512.save(OUT_DIR / "icon@2x.png")
    icon_512.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "icon.png")

    # --- logo.png / logo@2x.png: glyph + "avant" wordmark (light theme) ---
    logo = crop_content(cutout, *WORDMARK_ROWS)
    text_top_ratio = (WORDMARK_TEXT_START_ROW - WORDMARK_ROWS[0]) / (
        WORDMARK_ROWS[1] - WORDMARK_ROWS[0]
    )
    logo_512 = resize_height(logo, 512)
    logo_512.save(OUT_DIR / "logo@2x.png")
    resize_height(logo, 256).save(OUT_DIR / "logo.png")

    # --- dark_logo.png / dark_logo@2x.png: wordmark recolored to white ---
    dark_logo = recolor_wordmark_to_white(logo, text_top_ratio)
    dark_logo_512 = resize_height(dark_logo, 512)
    dark_logo_512.save(OUT_DIR / "dark_logo@2x.png")
    resize_height(dark_logo, 256).save(OUT_DIR / "dark_logo.png")

    print(f"wrote icon.png/icon@2x.png ({icon_512.size}) to {OUT_DIR}")
    print(f"wrote logo.png/logo@2x.png ({logo_512.size}) to {OUT_DIR}")
    print(f"wrote dark_logo.png/dark_logo@2x.png ({dark_logo_512.size}) to {OUT_DIR}")


if __name__ == "__main__":
    main()
