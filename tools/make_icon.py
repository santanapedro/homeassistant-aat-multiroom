"""Generates the integration's brand icon: a generic speaker/sound-waves
glyph, drawn with plain PIL shapes - no SVG dependency, no third-party
artwork, and deliberately NOT the AAT logo/trademark (this is an
unofficial integration, not affiliated with AAT).

Run from the repo root:

    python tools/make_icon.py

Writes custom_components/aat_multiroom/brand/icon.png (256x256) and
icon@2x.png (512x512), per the Home Assistant brand image spec
(https://github.com/home-assistant/brands, and the local-brand-images
support added in HA 2026.3 - see
https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).
Since the icon is square, no separate logo.png is needed - the icon is
used as the logo fallback automatically.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG_COLOR = (23, 92, 122, 255)  # a slate teal, distinct from AAT's brand red
FG_COLOR = (255, 255, 255, 255)

OUT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "aat_multiroom" / "brand"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Circular badge background, ~94% of the canvas.
    pad = size * 0.03
    draw.ellipse([pad, pad, size - pad, size - pad], fill=BG_COLOR)

    cx, cy = size * 0.46, size * 0.5

    # Speaker driver body (small rectangle).
    box_w, box_h = size * 0.14, size * 0.22
    box = [cx - box_w * 1.35, cy - box_h / 2, cx - box_w * 0.35, cy + box_h / 2]
    draw.rectangle(box, fill=FG_COLOR)

    # Speaker cone (trapezoid opening to the right).
    inner_half = box_h / 2
    outer_half = size * 0.19
    cone_x0 = box[2]
    cone_x1 = cx + size * 0.08
    cone = [
        (cone_x0, cy - inner_half),
        (cone_x1, cy - outer_half),
        (cone_x1, cy + outer_half),
        (cone_x0, cy + inner_half),
    ]
    draw.polygon(cone, fill=FG_COLOR)

    # Sound-wave arcs radiating to the right.
    wave_cx, wave_cy = cone_x1, cy
    for i, radius in enumerate((size * 0.09, size * 0.17, size * 0.25)):
        width = max(2, round(size * 0.028))
        alpha = 255 - i * 45
        color = (255, 255, 255, alpha)
        bbox = [wave_cx - radius, wave_cy - radius, wave_cx + radius, wave_cy + radius]
        draw.arc(bbox, start=-42, end=42, fill=color, width=width)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon_512 = draw_icon(512)
    icon_512.save(OUT_DIR / "icon@2x.png")
    icon_256 = icon_512.resize((256, 256), Image.LANCZOS)
    icon_256.save(OUT_DIR / "icon.png")
    print(f"wrote {OUT_DIR / 'icon.png'} (256x256) and {OUT_DIR / 'icon@2x.png'} (512x512)")


if __name__ == "__main__":
    main()
