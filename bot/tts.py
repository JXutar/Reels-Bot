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


async def _edge_words(text: str, voice: str, rate: str, out: Path) -> list[tuple[str, float, float]]:
    import edge_tts

    words = []
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((chunk["text"], start, start + chunk["duration"] / 1e7))
    return words


def _even_words(text: str, total: float) -> list[tuple[str, float, float]]:
    """Fallback timing: spread words across the clip by length."""
    words = text.split()
    weights = [len(w) + 2 for w in words]
    t, out, scale = 0.05, [], (total - 0.1) / max(1, sum(weights))
    for w, wt in zip(words, weights):
        out.append((w.strip(".,!?;:"), t, t + wt * scale))
        t += wt * scale
    return out


def synthesize_with_words(text: str, out: Path, voice: str, rate: str = "+0%", fallback: str | None = None,
                          dummy: bool = False) -> list[tuple[str, float, float]]:
    """Make the voiceover and return (word, start_sec, end_sec) for each spoken word."""
    if dummy:
        synthesize(text, out, voice, dummy=True)
        return _even_words(text, duration(out))
    last_error = None
    for v in [voice] + ([fallback] if fallback else []):
        for _attempt in range(3):
            try:
                words = asyncio.run(_edge_words(text, v, rate, out))
                if out.exists() and out.stat().st_size > 0:
                    return words or _even_words(text, duration(out))
            except Exception as e:
                last_error = e
    raise RuntimeError(f"Text-to-speech failed: {last_error}")


def duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(result.stdout.strip())
