"""Finding background videos and pop-up images.

Background video, in order of preference:
  1. script "background": "file:my-clip.mp4"   -> content/assets/backgrounds/my-clip.mp4
  2. any clip in content/assets/backgrounds/   (e.g. your own Minecraft parkour recording)
  3. Pexels stock video search (needs PEXELS_API_KEY), using the script's "background" keywords
     or the profile's default search
  4. nothing found -> a plain gradient (the renderer handles this)

Pop-up image for a scene ("image" field):
  "file:chart.png"  -> content/assets/images/chart.png
  "hospital bill"   -> Pexels photo search (needs PEXELS_API_KEY)
"""
from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import ASSETS_DIR, OUT_DIR

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
CACHE = OUT_DIR / "_cache"


def _pexels_key() -> str:
    return os.environ.get("PEXELS_API_KEY", "").strip()


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def _slug(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- background video
def pick_background(script: dict, profile: dict, rng: random.Random) -> tuple[Path | None, str]:
    """Returns (video path or None, description of where it came from)."""
    bg_cfg = profile.get("background", {})
    folder = ASSETS_DIR / "backgrounds"
    want = (script.get("background") or "").strip()

    if want.startswith("file:"):
        path = folder / want[5:].strip()
        if path.exists():
            return path, f"your clip {path.name}"
        print(f"  (background file not found: {path}, falling back)")
        want = ""

    if bg_cfg.get("use_own_clips", True) and folder.exists():
        clips = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT)
        if clips:
            clip = rng.choice(clips)
            return clip, f"your clip {clip.name}"

    query = want or bg_cfg.get("pexels_query", "")
    if query and _pexels_key():
        try:
            return _pexels_video(query, rng), f"Pexels video for '{query}'"
        except Exception as e:
            print(f"  (Pexels video search failed: {e})")
    return None, "plain gradient"


def _pexels_video(query: str, rng: random.Random) -> Path:
    r = requests.get("https://api.pexels.com/videos/search",
                     params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15},
                     headers={"Authorization": _pexels_key()}, timeout=30)
    r.raise_for_status()
    videos = [v for v in r.json().get("videos", []) if v.get("duration", 0) >= 6]
    if not videos:
        raise RuntimeError("no results")
    video = rng.choice(videos[:8])
    files = [f for f in video["video_files"]
             if f.get("width") and f.get("height") and f["height"] > f["width"] and f["height"] <= 1920]
    files = files or video["video_files"]
    best = max(files, key=lambda f: (f.get("height") or 0))
    return _download(best["link"], CACHE / f"pexels-video-{video['id']}.mp4")


# ---------------------------------------------------------------- pop-up images
def image_card(spec: str, rng: random.Random, out: Path, max_w: int = 860, max_h: int = 620) -> Path | None:
    """Turns an image spec into a sticker-style card PNG (rounded, white border, shadow)."""
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        if spec.startswith("file:"):
            src = ASSETS_DIR / "images" / spec[5:].strip()
            if not src.exists():
                print(f"  (image file not found: {src})")
                return None
        elif _pexels_key():
            src = _pexels_photo(spec, rng)
        else:
            return None
        img = Image.open(src).convert("RGB")
    except Exception as e:
        print(f"  (image '{spec}' skipped: {e})")
        return None

    # fit inside the box, keeping a pleasant landscape-ish crop for very tall photos
    w, h = img.size
    if h > w * 1.25:
        img = ImageOps.fit(img, (w, int(w * 1.0)), centering=(0.5, 0.4))
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    w, h = img.size

    border, radius, pad = 14, 40, 40
    card = Image.new("RGBA", (w + 2 * border, h + 2 * border), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, card.width - 1, card.height - 1), radius=radius,
                                           fill=(255, 255, 255, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius - border, fill=255)
    card.paste(img, (border, border), mask)

    canvas = Image.new("RGBA", (card.width + 2 * pad, card.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 14, pad + card.width, pad + card.height + 14),
                                             radius=radius, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(18)))
    canvas.alpha_composite(card, (pad, pad))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def _pexels_photo(query: str, rng: random.Random) -> Path:
    r = requests.get("https://api.pexels.com/v1/search",
                     params={"query": query, "orientation": "landscape", "per_page": 6},
                     headers={"Authorization": _pexels_key()}, timeout=30)
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"no Pexels photo for '{query}'")
    photo = rng.choice(photos[:4])
    return _download(photo["src"]["large"], CACHE / f"pexels-photo-{photo['id']}.jpg")
