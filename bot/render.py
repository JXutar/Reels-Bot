"""Turns a script into a vertical reel: voiceover per scene + slide per scene -> one mp4.

Output meets Instagram Reels specs: H.264, AAC, 30 fps, 1080x1920 (9:16).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import slides, tts
from .config import OUT_DIR, ROOT

FPS = 30
PAD = 0.45  # seconds of breathing room after each scene's narration
FADE = 0.25


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def render(script: dict, profile: dict, dummy_tts: bool = False) -> Path:
    style, voice = profile["style"], profile["voice"]
    work = OUT_DIR / script["id"]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

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

    final = OUT_DIR / f"{script['id']}.mp4"
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
