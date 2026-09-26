#!/usr/bin/env python3
"""Compose the banner from the real assets.

Every part of this is the product rather than a picture of it:

* the emblem is the shipped art (`docs/art/emblem.png`)
* the panel is the addon's own panel, rendered by `panel_preview.py` from the
  palette in `UI.lua`, against a made-up roster - so it looks exactly like the game
  and contains nobody's sessions, which a screenshot cannot promise
* the wordmark is drawn in a real font, because image models garble lettering

The background is generated here rather than asked of a model: a navy field with a
soft accent glow, drawn in float and dithered, so it does not band the way an 8-bit
gradient does at this size.

Run: python3 scripts/make_banner.py [--out docs/art/store-banner.png]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - the message is the feature
    print("numpy and Pillow are needed: pip install numpy pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_icon  # noqa: E402  (the font resolution and the palette live there)
import panel_preview  # noqa: E402

WIDTH, HEIGHT = 1280, 640  # GitHub's social preview, and most store headers

NAVY = (13, 18, 32)
ACCENT = (47, 107, 216)

# Panel: the previewer renders 560x540 with 30px of slack for its own glow, so the
# shot is 620x600 and the panel inside is 90% of it. Scaled to a 500px box.
PANEL_BOX = 580
PANEL_SLACK = 600 / 620  # the shot's height as a share of its width

EMBLEM_HEIGHT = 280
GAP = 120
WORDMARK_SIZE = 60
WORDMARK = "Agent Board"


def render_panel(work: Path, scale: int = 2) -> Path:
    """The panel as a PNG, via a headless browser of the real HTML."""
    page = work / "panel.html"
    page.write_text(panel_preview.render_bare(panel_preview.demo_board(), mode="board"), encoding="utf-8")

    shot = work / "panel.png"
    viewport = (620, 600)
    command = [
        "chromium", "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--force-device-scale-factor={scale}",
        "--default-background-color=00000000",  # transparent, so only the panel shows
        f"--window-size={viewport[0]},{viewport[1]}",
        f"--screenshot={shot}",
        page.as_uri(),
    ]

    probe = subprocess.run(command, capture_output=True, text=True)
    if not shot.is_file():
        # Older chromium spells the same thing without the `=new`.
        probe = subprocess.run([c.replace("--headless=new", "--headless") for c in command],
                               capture_output=True, text=True)
    if not shot.is_file():
        raise RuntimeError(f"chromium produced no screenshot: {probe.stderr.strip()[:300]}")
    return shot


def background(width: int, height: int) -> Image.Image:
    """Navy, a soft accent glow low-left, and enough dither to stop it banding."""
    ys, xs = np.mgrid[0:height, 0:width].astype(float)

    base = np.zeros((height, width, 3), dtype=float)
    base[:, :] = NAVY

    # A wide glow from the bottom-left, squared off so it fades rather than rings.
    radius = max(width, height) * 0.85
    distance = np.sqrt((xs - width * 0.04) ** 2 + (ys - height * 1.08) ** 2) / radius
    glow = np.clip(1.0 - distance, 0.0, 1.0) ** 2
    for channel, value in enumerate(ACCENT):
        base[:, :, channel] += (value - base[:, :, channel]) * glow * 0.42

    # A slight lift toward the top so it does not read as flat black.
    base += (1.0 - ys / height)[:, :, None] * 6.0

    # Half a level of noise. A gradient this slow bands in 8 bits, and banding is
    # the one artefact everyone notices on a store page.
    noise = np.random.default_rng(7).uniform(-0.5, 0.5, base.shape)
    return Image.fromarray(np.clip(base + noise, 0, 255).astype("uint8"), "RGB")


def compose(out: Path, emblem_path: Path) -> Path:
    canvas = background(WIDTH, HEIGHT).convert("RGBA")

    emblem = Image.open(emblem_path).convert("RGBA")
    scale = EMBLEM_HEIGHT / emblem.height
    emblem = emblem.resize((max(1, round(emblem.width * scale)), EMBLEM_HEIGHT), Image.LANCZOS)

    panel_shot = render_panel(Path(tempfile.mkdtemp(prefix="agent-board-banner-")))
    panel = Image.open(panel_shot).convert("RGBA")
    panel = panel.resize((round(PANEL_BOX * PANEL_SLACK), round(PANEL_BOX * PANEL_SLACK)), Image.LANCZOS)

    font = make_icon.load_font(WORDMARK_SIZE)
    draw = ImageDraw.Draw(canvas)
    box = draw.textbbox((0, 0), WORDMARK, font=font)
    width_left = max(emblem.width, box[2] - box[0])

    # Centre the whole composition: the left block, a gap, then the panel, with equal
    # margins. Measuring rather than placing by eye, so a longer wordmark or a wider
    # panel cannot push the thing off-centre.
    total = width_left + GAP + panel.width
    left = (WIDTH - total) // 2

    emblem_y = (HEIGHT - (EMBLEM_HEIGHT + 30 + WORDMARK_SIZE + 12)) // 2
    canvas.alpha_composite(emblem, (left + (width_left - emblem.width) // 2, emblem_y))

    text_top = emblem_y + EMBLEM_HEIGHT + 30 - box[1]

    offset = max(1, WORDMARK_SIZE // 40)
    draw.text((left + offset, text_top + offset), WORDMARK, font=font, fill=(0, 0, 0, 140))
    draw.text((left, text_top), WORDMARK, font=font, fill=(232, 237, 247, 255))

    canvas.alpha_composite(panel, (left + width_left + GAP, (HEIGHT - panel.height) // 2))

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, format="PNG")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "docs" / "art" / "store-banner.png"), help="where to write it")
    parser.add_argument("--emblem", default=str(ROOT / "docs" / "art" / "emblem.png"),
                        help="the emblem to place on the left")
    args = parser.parse_args()

    written = compose(Path(args.out), Path(args.emblem))
    print(f"banner {WIDTH}x{HEIGHT} -> {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
