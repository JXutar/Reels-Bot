"""Turns a script into a vertical reel (1080x1920, H.264/AAC, 30 fps - Instagram Reels spec).

Two styles, chosen by `video_style` in the profile (or per script):
  background  - background video (your clips or free stock video) + word-by-word subtitles
                + pop-up images per scene                                  [default]
  slides      - the original slideshow: one designed slide per scene
"""
from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

from . import captions, media, slides, tts
from .config import ASSETS_DIR, OUT_DIR, ROOT

FPS = 30
PAD = 0.45   # seconds of breathing room after each scene's narration
FADE = 0.25
IMAGE_Y = 640  # vertical centre of pop-up images
CAPTION_Y = 1180


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def render(script: dict, profile: dict, dummy_tts: bool = False) -> Path:
    style_name = script.get("video_style") or profile.get("video_style", "background")
    work = OUT_DIR / script["id"]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if style_name == "slides":
        joined = _render_slides(script, profile, work, dummy_tts)
    else:
        joined = _render_background(script, profile, work, dummy_tts)
    return _finish(joined, OUT_DIR / f"{script['id']}.mp4", profile["style"])


# ---------------------------------------------------------------- background style
def _render_background(script: dict, profile: dict, work: Path, dummy_tts: bool) -> Path:
    style, voice = profile["style"], profile["voice"]
    bg_cfg = profile.get("background", {})
    rng = random.Random(script["id"])  # same script -> same picks, so re-renders are stable

    # 1. voiceover per scene, with word timings, laid end to end
    scene_words, scene_spans, audio_parts, t = [], [], [], 0.0
    for i, scene in enumerate(script["scenes"]):
        mp3 = work / f"s{i:02d}.mp3"
        words = tts.synthesize_with_words(scene["voice"], mp3, voice["name"], voice.get("rate", "+0%"),
                                          voice.get("fallback"), dummy=dummy_tts)
        dur = tts.duration(mp3) + PAD
        wav = work / f"s{i:02d}.wav"
        _ff("-i", str(mp3), "-af", f"apad,atrim=0:{dur:.3f}", "-ar", "44100", "-ac", "2", str(wav))
        audio_parts.append(wav)
        scene_words.append([(w, t + s, t + e) for w, s, e in words])
        scene_spans.append((t, t + dur))
        t += dur
    total = t
    listing = work / "audio.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in audio_parts))
    voice_wav = work / "voice.wav"
    _ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(voice_wav))

    # 2. subtitles
    ass = captions.write_ass(scene_words, total, style, work / "captions.ass",
                             caption_y=CAPTION_Y, handle=style.get("handle", ""))

    # 3. background
    bg_path, bg_source = media.pick_background(script, profile, rng)
    print(f"  background: {bg_source}")
    inputs: list[str] = []
    if bg_path:
        bg_len = _probe_duration(bg_path)
        start = rng.uniform(0, max(0.0, bg_len - total - 1)) if bg_len > total + 1 else 0.0
        inputs += ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(bg_path)]
    else:
        still = work / "bg.png"
        slides._background(style).convert("RGB").save(still)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(still)]
    inputs += ["-i", str(voice_wav)]

    dim = float(bg_cfg.get("dim", 0.45)) if bg_path else 0.0
    graph = [f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps={FPS},"
             f"setsar=1,format=yuv420p" + (f",drawbox=c=black@{dim}:t=fill" if dim > 0 else "") + "[v0]"]
    last = "v0"

    # 4. pop-up images, one per scene that has "image"
    n_inputs = 2
    for i, scene in enumerate(script["scenes"]):
        card = media.image_card(scene.get("image", ""), rng, work / f"img{i:02d}.png")
        if not card:
            continue
        s0, s1 = scene_spans[i]
        s0 += 0.05
        show = max(0.5, s1 - s0 - 0.1)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{show:.3f}", "-i", str(card)]
        bounce = "if(lt(t,0.12),max(0.05,t/0.12*1.12),if(lt(t,0.24),1.12-(t-0.12)/0.12*0.12,1))"
        cw = _image_width(card)
        graph.append(f"[{n_inputs}:v]format=rgba,scale=w='max(2,trunc({cw}*{bounce}/2)*2)':h=-2:eval=frame,"
                     f"setpts=PTS+{s0:.3f}/TB[im{i}]")
        graph.append(f"[{last}][im{i}]overlay=x='(W-w)/2':y='{IMAGE_Y}-h/2':eval=frame:"
                     f"enable='between(t,{s0:.3f},{s0 + show:.3f})'[v{i + 1}]")
        last = f"v{i + 1}"
        n_inputs += 1

    fonts = str(ASSETS_DIR / "fonts").replace(":", "\\:")
    ass_path = str(ass).replace(":", "\\:")
    graph.append(f"[{last}]ass=filename='{ass_path}':fontsdir='{fonts}'[vout]")
    graph_file = work / "graph.txt"
    graph_file.write_text(";\n".join(graph))

    out = work / "joined.mp4"
    _ff(*inputs, "-filter_complex_script", str(graph_file), "-map", "[vout]", "-map", "1:a",
        "-t", f"{total:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", str(out))
    return out


def _image_width(path: Path) -> int:
    from PIL import Image
    with Image.open(path) as im:
        return im.width


def _probe_duration(path: Path) -> float:
    try:
        return tts.duration(path)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- slides style
def _render_slides(script: dict, profile: dict, work: Path, dummy_tts: bool) -> Path:
    style, voice = profile["style"], profile["voice"]
    scenes = script["scenes"]
    segments = []
    for i, scene in enumerate(scenes):
        png = slides.render_slide(scene, i, len(scenes), style, work / f"s{i:02d}.png")
        audio = tts.synthesize(scene["voice"], work / f"s{i:02d}.mp3", voice["name"], voice.get("rate", "+0%"),
                               voice.get("fallback"), dummy=dummy_tts)
        dur = tts.duration(audio) + PAD
        seg = work / f"s{i:02d}.mp4"
        vf = f"fade=t=in:st=0:d={FADE}" if i > 0 else "null"
        _ff("-loop", "1", "-framerate", str(FPS), "-i", str(png), "-i", str(audio),
            "-t", f"{dur:.3f}", "-vf", f"{vf},format=yuv420p",
            "-af", f"apad,atrim=0:{dur:.3f}", "-ar", "44100", "-ac", "2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k", str(seg))
        segments.append(seg)
    listing = work / "segments.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segments))
    joined = work / "joined.mp4"
    _ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined))
    return joined


# ---------------------------------------------------------------- shared
def _finish(joined: Path, final: Path, style: dict) -> Path:
    """Optional background music, then web-friendly mp4."""
    music = style.get("music")
    music_path = ROOT / music if music else None
    if music_path and music_path.exists():
        vol = style.get("music_volume", 0.1)
        _ff("-i", str(joined), "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex", f"[1:a]volume={vol}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(final))
    else:
        _ff("-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(final))
    return final
