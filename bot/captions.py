"""Word-by-word subtitles as an .ass file (rendered by ffmpeg/libass).

Words are grouped into short chunks (max ~3 words). While a chunk is on screen, the word
being spoken is shown bigger and in the accent colour; the others are white with a thick outline.
"""
from __future__ import annotations

from pathlib import Path


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = hex_color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def chunk_words(words: list[tuple[str, float, float]], max_words: int = 3, max_chars: int = 16):
    chunks, cur = [], []
    for w in words:
        text = w[0]
        if cur and (len(cur) >= max_words or sum(len(x[0]) + 1 for x in cur) + len(text) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if text.endswith((".", "?", "!", ",")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def write_ass(scene_words: list[list[tuple[str, float, float]]], total: float, style: dict, out: Path,
              caption_y: int = 1180, handle: str = "", handle_y: int = 1540,
              titles: list[tuple[str, float, float, str]] | None = None, title_y: int = 320) -> Path:
    """scene_words: per scene, a list of (word, start, end) in *global* seconds.
    titles: optional (text, start, end, kind) per scene; kind "title" = headline above the picture,
            "card" = big centred text (for silent scenes such as an end card). *word* = accent colour."""
    accent = _ass_color(style.get("accent", "#F5B942"))
    white = _ass_color(style.get("text", "#FFFFFF"))
    muted = _ass_color(style.get("muted", "#9FB3C8"), alpha=0x30)
    upper = style.get("caption_uppercase", True)
    size = int(style.get("caption_size", 92))
    font = style.get("caption_font", "Inter ExtraBold")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{size},{white},{white},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,7,4,5,80,80,0,1",
        f"Style: Handle,Inter Medium,36,{muted},{muted},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,40,40,0,1",
        f"Style: Title,{font},{int(style.get('title_size', 76))},{white},{white},&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,5,70,70,0,1",
        f"Style: Card,{font},{int(style.get('card_size', 84))},{white},{white},&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,5,90,90,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for words in scene_words:
        chunks = chunk_words(words)
        for ci, chunk in enumerate(chunks):
            chunk_end = chunks[ci + 1][0][1] if ci + 1 < len(chunks) else chunk[-1][2] + 0.25
            for wi, (word, start, _end) in enumerate(chunk):
                show_from = chunk[0][1] if wi == 0 else start
                show_to = chunk[wi + 1][1] if wi + 1 < len(chunk) else chunk_end
                parts = []
                for wj, (w2, _s, _e) in enumerate(chunk):
                    t = _escape(w2.upper() if upper else w2)
                    if wj == wi:
                        parts.append(f"{{\\c{accent}\\fscx112\\fscy112}}{t}{{\\r}}")
                    else:
                        parts.append(t)
                pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
                text = f"{{\\pos(540,{caption_y})}}{pop}" + " ".join(parts)
                lines.append(f"Dialogue: 1,{_ts(show_from)},{_ts(show_to)},Cap,,0,0,0,,{text}")

    for text, start, end, kind in titles or []:
        if not text:
            continue
        body = _highlight(_escape(text.upper() if upper and kind == "title" else text), accent, white)
        if kind == "card":
            lines.append(f"Dialogue: 2,{_ts(start)},{_ts(end)},Card,,0,0,0,,{{\\pos(540,900)\\fad(250,200)}}{body}")
        else:
            plain = len(text.replace("*", ""))
            fs = int(style.get("title_size", 76)) if plain <= 24 else 62 if plain <= 44 else 52 if plain <= 64 else 44
            lines.append(f"Dialogue: 2,{_ts(start)},{_ts(end)},Title,,0,0,0,,"
                         f"{{\\an2\\pos(540,{title_y})\\fs{fs}\\fad(200,150)}}{body}")
    if handle:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(total)},Handle,,0,0,0,,{{\\pos(540,{handle_y})}}{_escape(handle)}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _highlight(text: str, accent: str, base: str = "&H00FFFFFF") -> str:
    """*word* -> accent colour (ASS override tags). Only the colour changes, so size stays the same."""
    out, on = [], False
    for part in text.split("*"):
        if on and part:
            out.append(f"{{\\c{accent}}}{part}{{\\c{base}}}")
        else:
            out.append(part)
        on = not on
    return "".join(out)
