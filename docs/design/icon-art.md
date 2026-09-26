# Icon art

Everything image-shaped the project needs, in the order it matters, with prompts
that produce art matching the panel's existing palette rather than a generic app
icon dropped next to it.

Three of the four are optional. Only the first is worth getting right: the emblem
is the whole visual identity, and the others are that emblem re-composed.

## The palette, taken from the running UI

The panel's dark skin, so generated art and drawn UI agree. These are the exact
values from `THEMES.dark` in `addon/HermesAI/UI.lua`, not approximations:

| Role | Hex | Where it already appears |
| --- | --- | --- |
| Deep navy (the panel) | `#0D1220` | every pane's background |
| Accent blue | `#2F6BD8` | the panel border, the active tab |
| Gold | `#C7A359` | the crest `H`, the idle badge border |
| Text | `#E8EDF7` | titles |
| Muted | `#949EB8` | the trail line under each row |

Gold on deep navy is the identity: that pairing is already on screen in the
crest, the badge and the minimap button. Blue is an accent, not a second theme —
use it for at most one rim light.

## How to use these

**Attach the emblem you settled on to every later prompt.** A model that has not
seen it will invent a different H, and a set that does not share one letterform
stops looking like a family. Say "recompose the attached icon as …" and keep the
palette table for the colours.

**Check the result at 32 px before accepting it.** Downscale to 32, then zoom it
back up with nearest-neighbour so the pixels are visible:

```bash
magick emblem.png -resize 32x32 -filter point -resize 256x256 check.png
```

**Check the alpha channel, not the preview.** Every image viewer draws transparency
as a grey checkerboard, so a genuinely transparent export and a *painted* one look
identical on screen. Of the three exports of this emblem, two were real — mode
`RGBA`, alpha running 0 to 255 — and one was not: mode `RGB`, no alpha channel at
all, with the checkerboard drawn in as ~340 shades of grey. That one would have put
a grey grid in the corners of the addon-list icon.

```bash
python3 -c "from PIL import Image; i = Image.open('emblem.png'); print(i.mode, i.convert('RGBA').getchannel('A').getextrema())"
```

`RGBA (0, 255)` is real transparency. `RGB (255, 255)` is not — ask again and use
the words "transparent background with an alpha channel", because the caption is not
the file. Don't plan on cutting it out afterwards: on the pass this happened, 11% of
the pixels strictly inside the artwork matched the backdrop's colour signature, so
an automatic cut would have eaten into the plate.

The first pass of this emblem looked right at 64 and turned its wings into a jagged
blob glued to the H's stem at 32. The failure mode is always the same: fine detail
that reads at preview size and becomes noise at the size the client actually draws.
The fix is never "make it smaller" — it is negative space around the detail, or
removing it. One clear shape beats two that blur together.

Measured on the second and third passes, because the numbers say what the eye
cannot at 32px. A shape needs about 2px to survive there, which is 6% of the icon's
height:

| Element | In the 1030px plate | At 32px | Verdict |
| --- | --- | --- | --- |
| the H | 688px tall (67%) | 21px | reads perfectly |
| each wing | ~110px thick (11%) | 3.5px | thick enough |
| the gap between the wings | 30px (2.9%) | 1px | **merges them into one lump** |

So the wings were never the problem — the space between them was. A revision that
squarely hit the brief would either widen that gap to ~90px (3px at 32) or use a
single chevron. Worth knowing before spending a pass making the shapes bolder, which
would have changed nothing.

## 1. The emblem — the one that matters

**Prompt (copy this whole block):**

> A single flat vector-style icon for a World of Warcraft addon, centred on a
> fully transparent background, 512 by 512 pixels. Subject: a bold geometric
> monogram of the capital letter H, formed from two thick vertical bars and a
> crossbar, with the crossbar extending past the bars on the right into a short
> pair of swept-back wings — two tapered chevrons at most — suggesting a messenger
> in motion. The H is warm gold `#C7A359`, with a lighter gold `#E3C98A` highlight
> along its top and left bevels and a darker gold `#8C6F35` along the bottom and
> right, so it reads as gently embossed metal rather than flat colour. Behind it, a
> rounded square plate in deep navy `#0D1220` fills most of the frame, with a thin
> accent-blue `#2F6BD8` rim light along its top edge only. Style: clean vector
> shapes, hard crisp edges, thick strokes, low detail count, subtle inner bevel —
> the visual idiom of a WoW interface icon. Flat colour, no gradients that band, no
> photorealism, no 3D render, no drop shadow on the canvas, no outer glow.
> Absolutely no text, no lettering other than the single letter H, no numbers, no
> watermark, no border frame. The silhouette must stay legible when scaled down to
> 32 by 32 pixels, so keep every shape thick and remove fine detail.

Then ask for **nothing else in the same image** — no mockups, no UI, no words.

Why the constraints are there: an addon icon is drawn at 32–64px in a list, so
thin strokes and fine detail turn to mud; and image models happily add invented
lettering, which is the fastest way to make a release look amateurish.

## 2. The project avatar — 1024×1024, opaque

**Attach the emblem**, then:

> Recompose the attached icon as a 1024 by 1024 square, fully opaque, on a deep
> navy `#0D1220` background with a very subtle radial lift in the centre (about 6%
> lighter) so it does not read as a flat black square. Keep the same letterform,
> same gold palette, same bevel, same blue rim light — do not redraw the H, only
> re-frame it. The emblem sits centred with even padding of roughly 12% on every
> side. No text, no watermark, no border.

(If you are starting cold, describe it from prompt 1 instead — but expect a
different H.)

## 3. The social preview banner — 1280×640

**The shipped banner is not generated at all.** `make banner` composes it from
parts that are all the product:

| Layer | Where it comes from |
| --- | --- |
| background | drawn in code — navy with an accent glow, dithered so it cannot band |
| emblem | `docs/art/emblem.png`, the same art the icon comes from |
| panel | the addon's own panel, rendered by `panel_preview.py --demo` against a made-up roster |
| wordmark | a real font, via the same resolver the icon pipeline uses |

The panel is the point. A screenshot of the real board would show your session
titles and project names — the renderer uses a made-up roster instead, reads its
palette out of `UI.lua` so it cannot drift from the real thing, and produces a
picture that looks like the game without containing anyone's work.

The prompt below is kept for the alternative: an **art-led background with no
product shot on it**. It is what was used first, and a textless abstract banner is
a legitimate choice for a project that would rather not show a screenshot. If you
use it, it needs no wordmark from the model — `make banner` would have to be pointed
at it instead, which is a change to one constant in `make_banner.py`.

**Prompt, for that alternative:**

> A wide 1280 by 640 composition, fully opaque, deep navy `#0D1220` background
> with a faint accent-blue `#2F6BD8` glow in the lower left. The gold emblem from
> above is placed in the left third, occupying about 45% of the height, fully
> inside the frame. The right two thirds are left as quiet negative space with only
> a very subtle abstract suggestion of horizontal list rows — three or four thin,
> low-contrast rounded bars in slightly lighter navy, no text, no icons, no
> numbers. Flat vector style, crisp edges, no gradients that band, no text
> anywhere, no watermark.

**Attach the emblem** to this one too, and keep the right two thirds empty.

**Better than the abstract bars: a real screenshot.** The panel renderer and the
game itself both produce honest images, and a banner built from one plus the
emblem and a real wordmark looks like a product rather than a generated graphic.
Take a screenshot of the board in game, hand it over with the emblem, and the two
can be composited — the panel is a dark rectangle with a border, so it drops into
the right two thirds cleanly. Ask for the negative space either way.

**Leave the wordmark to a real font.** Image models garble lettering, and a
misspelled banner is worse than a textless one. Add `HermesAI` afterwards — the
`make banner` step below composites it in `#E8EDF7` using a system font, so the
letters are actually letters.

## 4. Optional: a custom minimap disc — 64×64, transparent

Only if you want to replace the client's own minimap-button disc and the gold `H`
glyph with your own art. The addon currently draws the game's disc with a font
glyph on top, deliberately, because it cannot break at an odd UI scale. **Prompt:**

> A circular minimap-button texture, 64 by 64, on a fully transparent background:
> a dark navy `#0D1220` disc that fills the frame edge to edge, with a thin gold
> `#C7A359` ring inset about 2 pixels from the edge, and the gold H emblem from
> above centred inside it at about 55% of the diameter. Soft antialiased edge, no
> outer glow, no drop shadow, no text other than the H.

## Turning the result into something the client reads

Generate at 512 or larger and let the resize happen here rather than asking the
model for a small image — models produce mush at 64px and good pixels resized
down. Then:

```bash
make icon MASTER=~/Downloads/emblem.png
```

That writes `addon/HermesAI/icon.tga` (64×64, 32-bit with alpha — the addon-list
icon), `addon/HermesAI/icon-128.png` (for the store page and the docs), and adds
`## IconTexture` to the toc if it is missing. `make package` then carries both,
because the client reads them by name rather than from the toc.

`.tga` is one of the two formats the client loads textures from, and Pillow writes
it losslessly with an alpha channel. If you would rather ship the native `.blp`,
install a BLP converter and use that instead — the wiring is the same.

If you only do one of these, do the emblem. A release with a good 64×64 icon and
no banner looks considered; a release with a banner and a placeholder icon does
not.
