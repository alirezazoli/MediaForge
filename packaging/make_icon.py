"""Draws the application icon: `python packaging/make_icon.py`.

Generated rather than hand-drawn so it can be re-rendered at any size and changed in
one place. Drawn at 4x and downsampled, which is what gives the small sizes clean edges;
each size in the .ico is rendered separately rather than letting Windows shrink the
largest one into mush at 16 px.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "mediaforge" / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

TOP_LEFT = (66, 133, 244)        # Google blue 500
BOTTOM_RIGHT = (11, 87, 208)     # Material 3 primary
SPARK = (251, 188, 4)            # Google yellow


def _gradient(size: int) -> Image.Image:
    gradient = Image.new("RGB", (size, size))
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            pixels[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP_LEFT, BOTTOM_RIGHT))
    return gradient


def render(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    unit = canvas_size / 256

    tile = Image.new("L", (canvas_size, canvas_size), 0)
    ImageDraw.Draw(tile).rounded_rectangle(
        [8 * unit, 8 * unit, 248 * unit, 248 * unit], radius=56 * unit, fill=255)

    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    image.paste(_gradient(canvas_size), (0, 0), tile)

    # Film frame: a rounded outline with sprocket notches, the "media" in the name.
    draw = ImageDraw.Draw(image)
    white = (255, 255, 255, 255)
    frame = [52 * unit, 70 * unit, 204 * unit, 186 * unit]
    draw.rounded_rectangle(frame, radius=22 * unit, outline=white, width=round(13 * unit))
    for x in (84, 116, 148, 180):
        for y in (70, 186):
            draw.rectangle([(x - 7) * unit, (y - 4) * unit, (x + 7) * unit, (y + 4) * unit],
                           fill=(0, 0, 0, 0))

    # Play triangle, slightly right of centre so it reads optically centred.
    play = [(112 * unit, 102 * unit), (112 * unit, 154 * unit), (156 * unit, 128 * unit)]
    draw.polygon(play, fill=white)

    # The spark: what the AI adds. Four-point star at the top-right corner.
    cx, cy, outer, inner = 200 * unit, 58 * unit, 34 * unit, 9 * unit
    star = [(cx, cy - outer), (cx + inner, cy - inner), (cx + outer, cy), (cx + inner, cy + inner),
            (cx, cy + outer), (cx - inner, cy + inner), (cx - outer, cy), (cx - inner, cy - inner)]
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).polygon(star, fill=(0, 0, 0, 90))
    image.alpha_composite(halo.filter(ImageFilter.GaussianBlur(5 * unit)), (0, round(2 * unit)))
    ImageDraw.Draw(image).polygon(star, fill=SPARK + (255,))

    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    frames = [render(size) for size in SIZES]
    frames[-1].save(ASSETS / "mediaforge.ico", sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    frames[-1].save(ASSETS / "mediaforge.png")
    print(f"Wrote mediaforge/assets/mediaforge.ico ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
