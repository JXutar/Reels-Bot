"""Turns a script into a vertical reel (1080x1920, H.264/AAC, 30 fps - Instagram Reels spec).

Styles, chosen by `video_style` in the profile (or per script):
  ai_visuals  - an AI-generated picture per scene, framed with a slow zoom/pan over a blurred copy of itself,
                crossfades between scenes, a headline per scene and word-by-word subtitles   [default]
  background  - background video (your clips or free stock video) + subtitles + pop-up images
  slides      - the original slideshow: one designed slide per scene
"""
from __future__ import annotations

import hashlib
import re
import random
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

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
    style_name = script.get("video_style") or profile.get("video_style", "ai_visuals")
    work = OUT_DIR / script["id"]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if style_name == "slides":
        joined = _render_slides(script, profile, work, dummy_tts)
    elif style_name == "background":
        joined = _render_background(script, profile, work, dummy_tts)
    else:
        joined = _render_ai_visuals(script, profile, work, dummy_tts)
    return _finish(joined, OUT_DIR / f"{script['id']}.mp4", profile["style"])


# ---------------------------------------------------------------- shared: voiceover
def _voice_track(script: dict, profile: dict, work: Path, dummy_tts: bool):
    """Voiceover per scene, laid end to end. Silent scenes ("hold") and PAUSE beats become silence.
    Returns (words per scene in global seconds, (start, end) per scene, voice.wav, total seconds)."""
    voice = profile["voice"]
    scene_words, scene_spans, audio_parts, t = [], [], [], 0.0
    for i, scene in enumerate(script["scenes"]):
        wav = work / f"s{i:02d}.wav"
        spoken = (scene.get("voice") or "").strip()
        if spoken:
            mp3 = work / f"s{i:02d}.mp3"
            words = tts.synthesize_with_words(spoken, mp3, voice["name"], voice.get("rate", "+0%"),
                                              voice.get("fallback"), dummy=dummy_tts)
            dur = tts.duration(mp3) + PAD + float(scene.get("pause_after", 0))
            _ff("-i", str(mp3), "-af", f"apad,atrim=0:{dur:.3f}", "-ar", "44100", "-ac", "2", str(wav))
        else:
            words = []
            dur = float(scene.get("hold", 3.0)) + float(scene.get("pause_after", 0))
            _ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{dur:.3f}", str(wav))
        audio_parts.append(wav)
        scene_words.append([(w, t + s, t + e) for w, s, e in words])
        scene_spans.append((t, t + dur))
        t += dur
    listing = work / "audio.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in audio_parts))
    voice_wav = work / "voice.wav"
    _ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(voice_wav))
    return scene_words, scene_spans, voice_wav, t


# ---------------------------------------------------------------- background style
def _render_background(script: dict, profile: dict, work: Path, dummy_tts: bool) -> Path:
    style, voice = profile["style"], profile["voice"]
    bg_cfg = profile.get("background", {})
    rng = random.Random(script["id"])  # same script -> same picks, so re-renders are stable

    scene_words, scene_spans, voice_wav, total = _voice_track(script, profile, work, dummy_tts)

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


# ---------------------------------------------------------------- AI-visuals style
FRAME = 960          # size of the framed picture
FRAME_TOP = 430      # y of the picture's top edge
XFADE = 0.35         # crossfade length between scenes
AI_TITLE_Y = FRAME_TOP - 34   # headline sits just above the picture and grows upward
NO_TEXT = ("Absolutely no text, no words, no letters, no numbers, no captions, no signs, no labels, "
           "no speech bubbles anywhere in the image.")
AI_CAPTION_Y = 1530
AI_HANDLE_Y = 1625


def _is_card(scene: dict) -> bool:
    """Silent text-only scenes (e.g. an end card) are shown as big centred text, no picture."""
    vis = (scene.get("visual") or "").lower()
    return not (scene.get("voice") or "").strip() and (not vis or "end card" in vis or "title card" in vis)


def _scene_picture(script: dict, scene: dict, i: int, profile: dict, work: Path, rng: random.Random):
    """Returns (path, source) for the scene's picture: your file -> AI image -> stock photo -> None."""
    vis_cfg = profile.get("visuals", {})
    spec = (scene.get("image") or "").strip()
    if spec.startswith("file:"):
        path = ASSETS_DIR / "images" / spec[5:].strip()
        if path.exists():
            return path, "your image"
    prompt = clean_visual(scene.get("visual") or scene.get("_auto_visual") or "")
    if not prompt and scene.get("image"):
        prompt = f"an illustration of {clean_visual(scene['image'])}"
    if not prompt:  # last resort: draw the scene's idea (described, never written)
        idea = clean_visual((scene.get("text") or scene.get("voice") or "").replace("*", ""))
        prompt = f"a simple symbolic illustration that represents the idea: {idea}" if idea else \
            "a calm abstract illustration"
    if prompt and media.ai_available():
        full = prompt
        if script.get("character") and script["character"].lower() not in prompt.lower():
            full += f". Main character: {script['character']}"
        style = vis_cfg.get("style", "")
        if style:
            full += f". Style: {style}"
        full += ". " + NO_TEXT
        seed = int(hashlib.md5(script["id"].encode()).hexdigest()[:8], 16) % 2_000_000_000
        for attempt in range(2):
            try:
                path = media.ai_image(full if attempt == 0 else full + " Bright, colourful, clearly visible subject.",
                                      work / f"ai{i:02d}.png", seed=seed + attempt, steps=int(vis_cfg.get("steps", 6)))
            except Exception as e:
                print(f"  (scene {i + 1}: AI image failed, using stock instead: {e})")
                break
            if not _looks_blank(path):
                return path, "AI"
            print(f"  (scene {i + 1}: AI picture came back blank, retrying)")
    stock = media.stock_photo(scene.get("image") or prompt, rng)
    if stock:
        return stock, "stock"
    return None, "none"


def _looks_blank(path: Path) -> bool:
    from PIL import ImageStat
    with Image.open(path) as im:
        st = ImageStat.Stat(im.convert("L").resize((64, 64)))
    return st.stddev[0] < 6 or st.mean[0] < 12


def clean_visual(text: str) -> str:
    """Picture prompts must describe things to draw, never words to write: drop quoted text and numbers."""
    t = re.sub(r"[\"“”'‘’][^\"“”'‘’]{1,80}[\"“”'‘’]", " ", text)        # "14,000", 'Text'
    t = re.sub(r"\b\d[\d,.:%–-]*\b", " ", t)                             # 28,000 / 0–20 / 20:00
    t = re.sub(r"(?i)\b(text|caption|label|labeled|labelled|title|words?|writing|says?|reads?)\b[^.,;]*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:-")
    return t


def _prepare_frames(pic: Path | None, style: dict, work: Path, i: int, last_bg: Path | None):
    """Make the blurred full-screen background and the square picture (or reuse the last background)."""
    bg_path, sq_path = work / f"bg{i:02d}.png", work / f"sq{i:02d}.png"
    if pic is None:
        clean_prev = last_bg.with_name(last_bg.stem + "_clean.png") if last_bg is not None else None
        if clean_prev is not None and clean_prev.exists():
            shutil.copy(clean_prev, bg_path)
        else:
            slides._background(style).convert("RGB").save(bg_path)
        shutil.copy(bg_path, bg_path.with_name(bg_path.stem + "_clean.png"))
        return bg_path, None
    img = Image.open(pic).convert("RGB")
    w, h = img.size
    side = min(w, h)
    sq = img.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
    sq.resize((1920, 1920), Image.LANCZOS).save(sq_path)  # big source = smooth zoom without jitter
    # background: cover 1080x1920, heavy blur, darkened
    scale = max(1080 / w, 1920 / h)
    bg = img.resize((int(w * scale) + 1, int(h * scale) + 1), Image.LANCZOS)
    left, top = (bg.width - 1080) // 2, (bg.height - 1920) // 2
    bg = bg.crop((left, top, left + 1080, top + 1920)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.45)
    bg.save(bg_path.with_name(bg_path.stem + "_clean.png"))  # shadow-free copy for text-only scenes
    # soft shadow under where the picture sits
    shadow = Image.new("L", (1080, 1920), 0)
    x0 = (1080 - FRAME) // 2
    ImageDraw.Draw(shadow).rounded_rectangle((x0, FRAME_TOP + 18, x0 + FRAME, FRAME_TOP + FRAME + 18),
                                             radius=48, fill=170)
    bg.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(24)))
    bg.save(bg_path)
    return bg_path, sq_path


def _fill_missing_visuals(script: dict, profile: dict) -> None:
    """Older scripts have no picture descriptions: ask the AI writer for them once (not saved to the file)."""
    from . import writer
    if not writer.available():
        return
    try:
        prompts = writer.visual_prompts(script, profile)
        for sc, pr in zip(script["scenes"], prompts):
            if pr and not sc.get("visual"):
                sc["_auto_visual"] = pr
    except Exception as e:
        print(f"  (couldn't write picture descriptions, using keywords: {e})")


def _rounded_mask(path: Path) -> Path:
    if not path.exists():
        m = Image.new("L", (FRAME, FRAME), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, FRAME - 1, FRAME - 1), radius=44, fill=255)
        m.save(path)
    return path


def _render_ai_visuals(script: dict, profile: dict, work: Path, dummy_tts: bool) -> Path:
    style = profile["style"]
    rng = random.Random(script["id"])
    scene_words, spans, voice_wav, total = _voice_track(script, profile, work, dummy_tts)
    mask = _rounded_mask(work / "mask.png")
    if media.ai_available() and any(not s.get("visual") for s in script["scenes"]):
        _fill_missing_visuals(script, profile)

    # 1. one video segment per scene (picture with slow zoom/pan over its blurred self)
    segments, titles, last_bg, sources = [], [], None, []
    n = len(script["scenes"])
    for i, scene in enumerate(script["scenes"]):
        start, end = spans[i]
        seg_len = (end - start) + (XFADE if i < n - 1 else 0)
        card = _is_card(scene)
        pic, src = (None, "card") if card else _scene_picture(script, scene, i, profile, work, rng)
        sources.append(src)
        bg, sq = _prepare_frames(pic, style, work, i, last_bg)
        last_bg = bg
        frames = max(1, int(round(seg_len * FPS)))
        seg = work / f"seg{i:02d}.mp4"
        if sq:
            zoom_in = i % 2 == 0
            z = f"1+0.10*on/{frames}" if zoom_in else f"1.10-0.10*on/{frames}"
            drift = ["0", "1", "-1"][i % 3]  # gentle sideways pan, varies by scene
            x = f"iw/2-(iw/zoom/2)+({drift})*(iw/zoom/2)*0.08*on/{frames}"
            graph = (f"[1:v]zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={FRAME}x{FRAME}:fps={FPS},"
                     f"format=rgba[fg0];[fg0][2:v]alphamerge[fg];"
                     f"[0:v][fg]overlay=x={(1080 - FRAME) // 2}:y={FRAME_TOP}:shortest=1,format=yuv420p[v]")
            _ff("-loop", "1", "-framerate", str(FPS), "-t", f"{seg_len:.3f}", "-i", str(bg),
                "-loop", "1", "-framerate", str(FPS), "-t", f"{seg_len:.3f}", "-i", str(sq),
                "-loop", "1", "-framerate", str(FPS), "-t", f"{seg_len:.3f}", "-i", str(mask),
                "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(FPS),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(seg))
        else:
            _ff("-loop", "1", "-framerate", str(FPS), "-t", f"{seg_len:.3f}", "-i", str(bg),
                "-vf", "format=yuv420p", "-frames:v", str(frames), "-r", str(FPS),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(seg))
        segments.append(seg)
        titles.append((scene.get("text", "") if (card or scene.get("text") != scene.get("voice", "")[:80]) else "",
                       start + (0.0 if i == 0 else XFADE * 0.5), end, "card" if card else "title"))
    print(f"  pictures: " + ", ".join(f"{k} x{sources.count(k)}" for k in sorted(set(sources))))

    # 2. crossfade the segments together (each transition starts exactly when the next scene starts)
    if len(segments) == 1:
        video = segments[0]
    else:
        inputs, chain = [], []
        for seg in segments:
            inputs += ["-i", str(seg)]
        prev = "0:v"
        for i in range(1, len(segments)):
            out = f"x{i}" if i < len(segments) - 1 else "vout"
            chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XFADE}:offset={spans[i][0]:.3f}[{out}]")
            prev = out
        video = work / "video.mp4"
        (work / "xfade.txt").write_text(";\n".join(chain))
        _ff(*inputs, "-filter_complex_script", str(work / "xfade.txt"), "-map", "[vout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), str(video))

    # 3. headlines + word-by-word subtitles + handle, then add the voice
    ass = captions.write_ass(scene_words, total, style, work / "captions.ass", caption_y=AI_CAPTION_Y,
                             handle=style.get("handle", ""), handle_y=AI_HANDLE_Y,
                             titles=titles, title_y=AI_TITLE_Y)
    fonts = str(ASSETS_DIR / "fonts").replace(":", "\\:")
    ass_path = str(ass).replace(":", "\\:")
    out = work / "joined.mp4"
    _ff("-i", str(video), "-i", str(voice_wav),
        "-vf", f"ass=filename='{ass_path}':fontsdir='{fonts}'", "-map", "0:v", "-map", "1:a",
        "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", str(out))
    return out


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
