#!/usr/bin/env python3
"""Turn generated emblem art into the addon icon.

An addon icon: a 512px-or-larger master down to `icon.tga` (64x64, the size the
client's addon list draws) plus `icon-128.png` for store pages, and the
`## IconTexture` line in the toc that points the client at it.

Resizing is done here rather than asked of the model: they produce mush at 64px and
good pixels resize down cleanly. The banner is a separate job with a separate
script (`make_banner.py`), because it composes the product rather than an emblem.

Run: python3 scripts/make_icon.py ~/Downloads/emblem.png
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageFont
except ImportError:  # pragma: no cover - the message is the feature
    print("Pillow is needed: pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
ADDON_NAME = "AgentBoard"
DEFAULT_ADDON = ROOT / "addon" / ADDON_NAME

ICON_SIZE = 64
ICON_LARGE = 128

# Font resolution for anything that draws text (the banner does). A hardcoded list
# of paths is how this first went wrong: none of the likely ones existed on this
# machine, Pillow fell back to its ~11px bitmap font, and the banner came out with
# an unreadable smear where the name should be. So ask the system what it uses,
# then look, and say so loudly if neither works.
FONT_PATTERNS = (
    "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/**/LiberationSans-Bold.ttf",
    "/usr/share/fonts/**/NotoSans-Bold.ttf",
    "/usr/share/fonts/**/*Bold*.ttf",
)

def find_font(explicit: str | None = None) -> Path | None:
    """A bold sans to draw the wordmark in, or None if this machine has none."""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None

    matched = shutil.which("fc-match")
    if matched:
        probe = subprocess.run([matched, "-f", "%{file}", "sans-serif:bold"],
                               capture_output=True, text=True)
        candidate = Path(probe.stdout.strip())
        if candidate.is_file() and candidate.suffix.lower() in (".ttf", ".otf", ".ttc"):
            return candidate

    for pattern in FONT_PATTERNS:
        for candidate in sorted(Path("/").glob(pattern.lstrip("/"))):
            return candidate
    return None


def load_font(size: int, explicit: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font(explicit)
    if path is None:
        print("warning: no TrueType font found, so the wordmark will be a tiny bitmap.\n"
              "         install one (fonts-liberation, dejavu-sans) or pass --font <path>",
              file=sys.stderr)
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def prepare(image: Image.Image, crop: bool = True) -> Image.Image:
    """The framed square to draw from: cut to the art, centred.

    A model asked for a 512 icon returns a bigger canvas with the art inset by
    whatever margin it felt like - 8% here. Left alone, that margin survives the
    resize and the icon the client draws at 32px is a third smaller than the space
    it was given. Blizzard's own icons are full bleed, so trim to the art and pad
    back to a centred square, which also keeps a lopsided composition centred.
    """
    if not crop:
        return image

    box = image.getbbox()
    if not box:
        return image

    art = image.crop(box)
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    return square


def write_icon(addon_dir: Path, master: Path, crop: bool = True) -> list[Path]:
    """The addon-list icon, at the two sizes worth keeping."""
    image = Image.open(master).convert("RGBA")

    # Said out loud rather than left to be discovered in the addon list: a master
    # without an alpha channel gives a solid square behind a rounded plate.
    if image.getchannel("A").getextrema() == (255, 255):
        print("note: this master has no transparency - the icon will be a solid square")

    image = prepare(image, crop)

    written: list[Path] = []
    small = addon_dir / "icon.tga"
    image.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS).save(small, format="TGA")
    written.append(small)

    large = addon_dir / "icon-128.png"
    image.resize((ICON_LARGE, ICON_LARGE), Image.LANCZOS).save(large, format="PNG")
    written.append(large)

    return written


def wire_toc(addon_dir: Path) -> bool:
    """Point the client at the icon. Returns whether the toc changed."""
    toc = addon_dir / f"{ADDON_NAME}.toc"
    lines = toc.read_text(encoding="utf-8").splitlines()

    if any(line.startswith("## IconTexture:") for line in lines):
        return False

    # After Notes, which is where the client's own addons put it.
    line = f"## IconTexture: Interface\\AddOns\\{ADDON_NAME}\\icon"
    for index, existing in enumerate(lines):
        if existing.startswith("## Notes:"):
            lines.insert(index + 1, line)
            break
    else:
        lines.insert(1, line)

    toc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("master", help="the generated PNG (512px or larger)")
    parser.add_argument("--addon", default=str(DEFAULT_ADDON), help="the addon folder to write into")
    parser.add_argument("--no-crop", action="store_true",
                        help="keep the master's own framing instead of trimming to the art")
    args = parser.parse_args()

    master = Path(args.master).expanduser()
    if not master.is_file():
        print(f"no such image: {master}", file=sys.stderr)
        return 1

    addon_dir = Path(args.addon)
    if not addon_dir.is_dir():
        print(f"no such addon folder: {addon_dir}", file=sys.stderr)
        return 1

    for path in write_icon(addon_dir, master, crop=not args.no_crop):
        print(f"wrote {path}")

    if wire_toc(addon_dir):
        print(f"added ## IconTexture to {addon_dir / (ADDON_NAME + '.toc')}")
    print("now: make package  (the client reads the icon by name, so it has to be in the zip)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
