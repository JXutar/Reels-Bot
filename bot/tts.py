"""Text-to-speech with edge-tts (free, no account). A silent 'dummy' mode exists for offline tests."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


async def _edge(text: str, voice: str, rate: str, out: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def synthesize(text: str, out: Path, voice: str, rate: str = "+0%", fallback: str | None = None,
               dummy: bool = False) -> Path:
    if dummy:
        seconds = max(1.5, len(text.split()) / 2.6)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.2f}", "-q:a", "9", str(out)], check=True)
        return out
    last_error = None
    for v in [voice] + ([fallback] if fallback else []):
        for _attempt in range(3):
            try:
                asyncio.run(_edge(text, v, rate, out))
                if out.exists() and out.stat().st_size > 0:
                    return out
            except Exception as e:  # network hiccups are common; retry
                last_error = e
    raise RuntimeError(f"Text-to-speech failed: {last_error}")


def duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(result.stdout.strip())
