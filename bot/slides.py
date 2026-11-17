"""Draws one 1080x1920 slide per scene with Pillow.

Words wrapped in *asterisks* in a scene's text are drawn in the accent colour.
Text is kept inside Instagram's safe zone (clear of the top bar and the caption area at the bottom).
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ASSETS_DIR

W, H = 1080, 1920
MARGIN_X = 96
TEXT_TOP, TEXT_BOTTOM = 420, 1380  # safe area for the main text


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = [ASSETS_DIR / "fonts" / f"{name}.otf", ASSETS_DIR / "fonts" / f"{name}.ttf",
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _background(style: dict) -> Image.Image:
    top, bottom = _hex(style["bg_top"]), _hex(style["bg_bottom"])
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    img = grad.resize((W, H)).convert("RGBA")
    # soft accent glow for depth
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((W - 520, -260, W + 380, 640), fill=_hex(style["accent"]) + (45,))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    return Image.alpha_composite(img, glow)


def _tokens(text: str) -> list[tuple[str, bool, bool]]:
    """Split text into (word, highlighted, glued) tuples. '*like this*' marks highlighted words.
    'glued' means no space before the word (e.g. a comma right after a highlighted word)."""
    out, highlighted, prev_space = [], False, True
    for part in re.split(r"(\*)", text):
        if part == "*":
            highlighted = not highlighted
            continue
        if not part:
            continue
        for j, word in enumerate(part.split()):
            glued = j == 0 and bool(out) and not prev_space and not part[0].isspace()
            out.append((word, highlighted, glued))
        prev_space = part[-1].isspace()
    return out


def _wrap(tokens, font, max_width, draw) -> list[list[tuple[str, bool]]]:
    lines, line = [], []
    space = draw.textlength(" ", font=font)
    width = 0.0
    for word, hl, glued in tokens:
        if word == "\\n":
            lines.append(line); line, width = [], 0.0
            continue
        w = draw.textlength(word, font=font)
        gap = 0 if (glued or not line) else space
        if line and not glued and width + gap + w > max_width:
            lines.append(line); line, width, gap = [], 0.0, 0
        width += gap + w
        line.append((word, hl, glued and bool(line)))
    if line:
        lines.append(line)
    return lines


def _fit(tokens, font_name, start_size, min_size, max_width, max_height, draw, line_gap=1.18):
    size = start_size
    while True:
        font = _font(font_name, size)
        lines = _wrap(tokens, font, max_width, draw)
        height = len(lines) * size * line_gap
        if height <= max_height or size <= min_size:
            return font, lines, size, line_gap
        size -= 4


def render_slide(scene: dict, index: int, total: int, style: dict, out: Path) -> Path:
    img = _background(style)
    d = ImageDraw.Draw(img)
    text_c, muted_c, accent_c = _hex(style["text"]), _hex(style["muted"]), _hex(style["accent"])
    bg_c = _hex(style["bg_bottom"])
    dim_c = tuple(int(bg_c[i] + (muted_c[i] - bg_c[i]) * 0.3) for i in range(3))
    kind = scene.get("style", "body")

    # series pill
    label = style.get("series_label", "")
    if label:
        f = _font(style["font_bold"], 34)
        tw = d.textlength(label, font=f)
        d.rounded_rectangle((MARGIN_X, 250, MARGIN_X + tw + 56, 312), radius=31, outline=accent_c, width=3)
        d.text((MARGIN_X + 28, 281), label, font=f, fill=accent_c, anchor="lm")

    # main text
    sizes = {"hook": (120, 72), "cta": (92, 60), "body": (88, 56)}
    start, minimum = sizes.get(kind, sizes["body"])
    tokens = _tokens(scene["text"].replace("\n", " \\n "))
    sub = scene.get("sub", "")
    sub_space = 220 if sub else 0
    font, lines, size, gap = _fit(tokens, style["font_bold"], start, minimum, W - 2 * MARGIN_X,
                                  TEXT_BOTTOM - TEXT_TOP - sub_space, d)
    block_h = len(lines) * size * gap + (sub_space if sub else 0)
    y = TEXT_TOP + (TEXT_BOTTOM - TEXT_TOP - block_h) / 2
    space = d.textlength(" ", font=font)
    for line in lines:
        x = MARGIN_X
        for k, (word, hl, glued) in enumerate(line):
            if k and not glued:
                x += space
            d.text((x, y), word, font=font, fill=accent_c if hl else text_c)
            x += d.textlength(word, font=font)
        y += size * gap
    if sub:
        sf = _font(style["font_regular"], 46)
        y += 36
        d.rectangle((MARGIN_X, y, MARGIN_X + 72, y + 6), fill=accent_c)
        y += 40
        for line in _wrap(_tokens(sub), sf, W - 2 * MARGIN_X, d):
            d.text((MARGIN_X, y), "".join(("" if g or not k else " ") + w for k, (w, _, g) in enumerate(line)), font=sf, fill=muted_c)
            y += 46 * 1.3

    # progress bar
    seg_gap, bar_y = 12, 1470
    seg_w = (W - 2 * MARGIN_X - seg_gap * (total - 1)) / total
    for i in range(total):
        x0 = MARGIN_X + i * (seg_w + seg_gap)
        d.rounded_rectangle((x0, bar_y, x0 + seg_w, bar_y + 8), radius=4,
                            fill=accent_c if i <= index else dim_c)

    handle = style.get("handle", "")
    if handle:
        d.text((MARGIN_X, bar_y + 44), handle, font=_font(style["font_regular"], 36), fill=muted_c)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return out
