"""Writing reel scripts with AI.

Two ways in:
  idea mode   - "/idea 60s: why medical cards have co-payment"  -> the AI writes the whole script
  script mode - you paste a full script (SCENE / VO: / Text: / Visual: / PAUSE lines, or /script ...)
                -> every scene and voiceover line is kept word for word; the AI only adds the title,
                   caption, hashtags and turns your "Visual:" notes into image prompts.

Which AI:
  - ANTHROPIC_API_KEY set -> Claude (paid per use)
  - else GEMINI_API_KEY set -> Gemini free tier (newest available "flash" model, falls back to others)
Script mode still works with no AI at all (simpler title/caption, your visuals used as-is).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import requests

from .config import validate_script

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

DEFAULT_BANNED = [
    "follow for", "follow to", "follow along", "follow and", "follow if", "follow me", "follow us",
    "give it a follow", "give me a follow", "hit follow", "hit the follow", "tap follow", "give me a follow",
    "like and subscribe", "subscribe", "smash that", "comment below", "share this with",
    "policies differ", "different policies", "every policy is different", "each policy is different",
    "policies handle", "policy handles", "check your policy", "check your own policy", "your policy wording",
    "policy wording", "read your policy", "read the fine print", "speak to your agent", "talk to your agent",
]


def available() -> str:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "Claude"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "Gemini"
    return ""


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "idea"


def unique_id(title: str, existing: set[str], today: datetime | None = None) -> str:
    today = today or datetime.now()
    base = f"{today:%Y-%m-%d}-{slugify(title)}"
    sid, n = base, 2
    while sid in existing:
        sid, n = f"{base}-{n}", n + 1
    return sid


# ---------------------------------------------------------------- banned phrases
def banned_list(profile: dict) -> list[str]:
    extra = profile.get("banned_phrases")
    phrases = DEFAULT_BANNED if extra is None else []
    return [p.lower() for p in phrases + (extra or [])]


def find_banned(text: str, banned: list[str]) -> list[str]:
    low = (text or "").lower()
    return [p for p in banned if p in low]


def scrub_text(text: str, banned: list[str]) -> str:
    """Drop whole sentences that contain a banned phrase."""
    if not text:
        return text
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    kept = [p for p in parts if p.strip() and not find_banned(p, banned)]
    sep = "\n" if "\n" in text else " "
    return sep.join(kept).strip()


def scrub_script(script: dict, banned: list[str]) -> dict:
    """Remove banned sentences everywhere; drop scenes that end up empty or are pure 'follow' endings."""
    scenes = []
    for sc in script["scenes"]:
        voice = scrub_text(sc.get("voice", ""), banned)
        text = scrub_text(sc.get("text", ""), banned)
        if sc.get("voice") and not voice:
            continue  # the whole spoken line was banned -> drop the scene
        if not voice and not text:
            continue
        sc = dict(sc, voice=voice, text=text or voice[:80])
        scenes.append(sc)
    if scenes and scenes[-1].get("style") == "cta" and len(scenes) > 1:
        scenes[-1].pop("style", None)
    script["scenes"] = scenes
    script["caption"] = scrub_text(script.get("caption", ""), banned)
    return script


# ---------------------------------------------------------------- script mode (parse what you wrote)
SCENE_RE = re.compile(r"^\s*(?:\*\*)?\s*scene\s*\d*\b.*$", re.I)
FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?(vo|voice|voiceover|voice-over|narration|text|on-screen text|onscreen|"
                      r"visual|visuals|image|shot)\s*:\s*(.*)$", re.I)
PAUSE_RE = re.compile(r"^\s*(?:\[)?pause\b(?:\s*(\d+(?:\.\d+)?)\s*s?)?(?:\])?.*$", re.I)


def looks_like_script(text: str) -> bool:
    scenes = len(re.findall(r"(?im)^\s*(?:\*\*)?\s*scene\s*\d", text))
    vos = len(re.findall(r"(?im)^\s*(?:[-*]\s*)?(?:vo|voice|voiceover)\s*:", text))
    return scenes >= 2 or vos >= 3


def _clean_line(v: str) -> str:
    v = v.strip().strip("*").strip()
    if len(v) >= 2 and v[0] in "\"“'" and v[-1] in "\"”'":
        v = v[1:-1].strip()
    return v


def parse_script_text(text: str) -> dict:
    """Parse a pasted script. Returns {"title_hint", "scenes": [...]}; keeps your words exactly."""
    title_hint, scenes, cur = "", [], None
    lines = text.replace("\r", "").split("\n")
    has_headers = any(SCENE_RE.match(l.strip()) for l in lines if l.strip())
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if SCENE_RE.match(line):
            cur = {"voice": "", "text": "", "visual": ""}
            scenes.append(cur)
            continue
        m = PAUSE_RE.match(line)
        if m and line.lower().startswith(("pause", "[pause")):
            target = cur if cur is not None else (scenes[-1] if scenes else None)
            if target is not None:
                target["pause_after"] = round(target.get("pause_after", 0) + float(m.group(1) or 1.2), 2)
            continue
        m = FIELD_RE.match(line)
        if m:
            key, val = m.group(1).lower(), _clean_line(m.group(2))
            if cur is None and has_headers:
                continue  # e.g. "Voice: calm, warm..." directions above SCENE 1 -> not a spoken line
            if cur is None:  # no SCENE headers at all -> each VO starts a scene
                cur = {"voice": "", "text": "", "visual": ""}
                scenes.append(cur)
            if key in ("vo", "voice", "voiceover", "voice-over", "narration"):
                if not has_headers and cur["voice"]:
                    cur = {"voice": "", "text": "", "visual": ""}
                    scenes.append(cur)
                if val.lower().strip("()[] .") in ("none", "no vo", "silent", "-", "n/a", ""):
                    val = ""
                cur["voice"] = (cur["voice"] + " " + val).strip()
            elif key in ("text", "on-screen text", "onscreen"):
                cur["text"] = (cur["text"] + " " + val).strip()
            else:
                cur["visual"] = (cur["visual"] + " " + val).strip()
            continue
        if cur is None and not title_hint and not re.match(r"(?i)^(length|voice|music|format|duration)\b", line):
            title_hint = re.sub(r"(?i)\s*[-–—]\s*video script\s*$", "", line).strip(" #*")
    out = []
    for sc in scenes:
        if not sc["voice"] and not sc["text"]:
            continue
        scene = {"voice": sc["voice"], "text": sc["text"] or sc["voice"][:80]}
        if sc["visual"]:
            scene["visual"] = sc["visual"]
        if not sc["voice"]:
            scene["hold"] = 3.0
        if sc.get("pause_after"):
            scene["pause_after"] = sc["pause_after"]
        out.append(scene)
    if out:
        out[0]["style"] = "hook"
    return {"title_hint": title_hint, "scenes": out}


# ---------------------------------------------------------------- prompts
def _length_spec(seconds: int | None) -> tuple[str, str]:
    seconds = seconds or 40
    lo, hi = max(3, round(seconds / 7)), max(5, round(seconds / 4.5))
    return f"{lo}-{min(hi, 20)}", f"about {seconds} seconds"


def _idea_prompt(idea: str, profile: dict, example: dict | None, seconds: int | None) -> str:
    rules = "\n".join(f"- {r}" for r in profile.get("rules", []))
    n_scenes, length = _length_spec(seconds)
    ex = json.dumps(example, ensure_ascii=False, indent=1) if example else "(none)"
    idea_line = idea.strip() or "Choose a fresh, specific topic that fits this channel."
    banned = ", ".join(f'"{b}"' for b in banned_list(profile)[:20])
    return f"""You write scripts for short vertical videos (Instagram Reels).

CHANNEL: {profile.get('description', profile['name'])}
CHANNEL RULES:
{rules or '- (none)'}

IDEA FROM THE CREATOR: {idea_line}

Write ONE reel script as JSON with exactly these keys:
- "title": short title (max 60 chars)
- "scenes": {n_scenes} objects, each with:
    - "voice": what the narrator says (natural spoken English, 1-3 sentences)
    - "text": a short on-screen headline for the scene (max 8 words); wrap 1-2 key words in *asterisks*
    - "visual": a vivid one-sentence description of a picture that illustrates this scene
      (for an AI image generator: subject, setting, mood; no logos, no real brands, no real people).
      The picture must contain NO text, words, numbers, signs or labels: show the idea with objects,
      people and scenery only (the headline carries any words or numbers)
    - "image": 1-3 word stock-photo search for the same idea (e.g. "hospital bed")
  The first scene must hook the viewer in the first 2 seconds. The last scene must land the ending
  (a twist, punchline or a clear takeaway). Total narration: {length} when read aloud.
- "caption": Instagram caption, 2-4 short lines, may use 1-2 emojis
- "hashtags": 3-5 relevant hashtags

NEVER include any call to action (no "follow", "like", "subscribe", "comment", "share").
NEVER tell viewers to check their policy, read the fine print, or say policies differ.
Avoid these phrases entirely: {banned}

Return ONLY the JSON object, no explanations.

Example of a previous script from this channel (format and tone only):
{ex}
"""


def _enrich_prompt(parsed: dict, profile: dict) -> str:
    scenes = [{"n": i + 1, "voice": s.get("voice", ""), "text": s.get("text", ""), "visual": s.get("visual", "")}
              for i, s in enumerate(parsed["scenes"])]
    return f"""You help publish a finished short-video script on Instagram Reels. Do NOT change any voice or text.

CHANNEL: {profile.get('description', profile['name'])}
WORKING TITLE: {parsed.get('title_hint') or '(none)'}
SCENES: {json.dumps(scenes, ensure_ascii=False)}

Return JSON with:
- "title": short title (max 60 chars)
- "caption": Instagram caption, 2-4 short lines, may use 1-2 emojis. No calls to action, no "check your policy".
- "hashtags": 3-5 relevant hashtags
- "character": if a recurring person appears (e.g. a named character), a short fixed visual description
  of them (age, gender, hair, clothing) to keep them consistent; otherwise ""
- "prompts": a list with one entry per scene, in order: a standalone one-sentence image description for an
  AI image generator, based on that scene's "visual" (or its voice if visual is empty). If the recurring
  character appears, describe them using the "character" description instead of their name.
  No logos, no real brands, no real people. The picture must contain NO text, words, numbers, signs or labels:
  turn things like "a counter showing 28,000" into a visual idea without readable writing
  (e.g. "a huge glowing mechanical counter with blurred spinning digits").
- "images": a list with one 1-3 word stock-photo search per scene, in order.

Return ONLY the JSON object."""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- providers
def _claude(prompt: str) -> str:
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                      headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                               "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": CLAUDE_MODEL, "max_tokens": 4000,
                            "messages": [{"role": "user", "content": prompt}]})
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"Claude API error: {data.get('error', data)}")
    return "".join(b.get("text", "") for b in data.get("content", []))


def _gemini_models(key: str) -> list[str]:
    """Free 'flash' text models, newest first. Uses an explicit GEMINI_MODEL if set."""
    if os.environ.get("GEMINI_MODEL", "").strip():
        return [os.environ["GEMINI_MODEL"].strip()]
    try:
        r = requests.get(f"{GEMINI}/models", params={"key": key, "pageSize": 200}, timeout=30)
        models = [m for m in r.json().get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        models = []
    names = [m["name"].split("/", 1)[-1] for m in models]
    bad = ("image", "tts", "live", "audio", "embedding", "thinking", "exp", "preview", "vision", "8b")
    flash = [n for n in names if "flash" in n and not any(b in n for b in bad)]

    def version(n: str):
        nums = re.findall(r"\d+(?:\.\d+)?", n)
        return float(nums[0]) if nums else 0.0

    latest_alias = [n for n in flash if n.endswith("-latest") and "lite" not in n]
    full = sorted([n for n in flash if "lite" not in n and n not in latest_alias], key=version, reverse=True)
    lite = sorted([n for n in flash if "lite" in n], key=version, reverse=True)
    ordered = latest_alias + full + lite
    return ordered or ["gemini-flash-latest", "gemini-2.0-flash"]


def _thinking_config(model: str) -> dict | None:
    """Ask for little/no 'thinking' so short scripts come back fast (field names differ by generation)."""
    nums = re.findall(r"\d+(?:\.\d+)?", model)
    v = float(nums[0]) if nums else 0.0
    if v >= 3:
        return {"thinkingLevel": "low"}
    if v >= 2.5:
        return {"thinkingBudget": 0}
    return None


def _gemini_call(key: str, model: str, prompt: str, thinking: dict | None, timeout: int):
    config = {"responseMimeType": "application/json", "temperature": 0.9}
    if thinking:
        config["thinkingConfig"] = thinking
    return requests.post(f"{GEMINI}/models/{model}:generateContent", params={"key": key}, timeout=timeout,
                         json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config})


def _gemini(prompt: str) -> str:
    key = os.environ["GEMINI_API_KEY"].strip()
    models = _gemini_models(key)
    lite = [m for m in models if "lite" in m]
    order = models[:1] + lite[:1] + [m for m in models[1:] if m not in lite[:1]]
    errors, started = [], time.time()
    for model in order[:5]:
        if time.time() - started > 360:
            errors.append("gave up after 6 minutes")
            break
        thinking = _thinking_config(model)
        try:
            r = _gemini_call(key, model, prompt, thinking, timeout=75)
            if r.status_code == 400 and thinking and "thinking" in r.text.lower():
                r = _gemini_call(key, model, prompt, None, timeout=75)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{model}: {type(e).__name__}")
            continue
        if r.status_code >= 400:
            errors.append(f"{model}: {data.get('error', {}).get('message', r.status_code)}")
            continue
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if text:
            print(f"  (written by {model})")
            return text
        errors.append(f"{model}: empty answer")
    raise RuntimeError("Gemini didn't answer: " + " | ".join(errors))


def _ask(prompt: str) -> str:
    provider = available()
    if not provider:
        raise RuntimeError("No AI writer configured. Add a GEMINI_API_KEY (free) or ANTHROPIC_API_KEY secret.")
    return (_claude if provider == "Claude" else _gemini)(prompt)


# ---------------------------------------------------------------- idea mode
def write_script(idea: str, profile: dict, existing_ids: set[str], example: dict | None = None,
                 seconds: int | None = None, today: datetime | None = None) -> dict:
    banned = banned_list(profile)
    prompt = _idea_prompt(idea, profile, example, seconds)
    last_error = None
    for attempt in range(3):
        try:
            data = _extract_json(_ask(prompt))
            script = {"id": unique_id(data.get("title") or idea, existing_ids, today), "profile": profile["name"],
                      "title": data["title"][:80], "source_idea": idea.strip(), "mode": "idea",
                      "scenes": [], "caption": data.get("caption", ""), "hashtags": data.get("hashtags", [])[:6]}
            for sc in data["scenes"][:20]:
                scene = {"voice": str(sc["voice"]).strip(), "text": str(sc.get("text") or sc["voice"])[:120]}
                if sc.get("visual"):
                    scene["visual"] = str(sc["visual"])[:400]
                if sc.get("image"):
                    scene["image"] = str(sc["image"])[:40]
                script["scenes"].append(scene)
            if script["scenes"]:
                script["scenes"][0]["style"] = "hook"
            hits = find_banned(" ".join([s["voice"] + " " + s["text"] for s in script["scenes"]]
                                        + [script["caption"]]), banned)
            if hits and attempt < 2:
                prompt += f"\n\nYour previous answer used forbidden phrases ({', '.join(sorted(set(hits)))}). " \
                          "Rewrite without them."
                continue
            script = scrub_script(script, banned)  # final safety net
            validate_script(script, "AI draft")
            return script
        except SystemExit as e:
            last_error = e
            prompt += f"\n\nYour previous answer was invalid ({e}). Follow the format exactly."
        except (KeyError, ValueError, TypeError, IndexError) as e:
            last_error = e
            prompt += "\n\nYour previous answer was not valid JSON with the required keys. Try again."
    raise RuntimeError(f"The AI's script wasn't usable: {last_error}")


# ---------------------------------------------------------------- script mode
def script_from_text(text: str, profile: dict, existing_ids: set[str], today: datetime | None = None) -> dict:
    """Keeps every scene and line you wrote; AI (if available) only adds title/caption/hashtags/image prompts."""
    parsed = parse_script_text(text)
    if len(parsed["scenes"]) < 1:
        raise RuntimeError("I couldn't find any scenes. Use lines like 'SCENE 1', 'VO: ...', 'Text: ...'.")
    scenes = parsed["scenes"]
    meta = {}
    if available():
        try:
            meta = _extract_json(_ask(_enrich_prompt(parsed, profile)))
        except Exception as e:
            print(f"  (AI enrichment skipped: {e})")
            meta = {}
    character = str(meta.get("character") or "").strip()
    prompts = meta.get("prompts") if isinstance(meta.get("prompts"), list) else []
    images = meta.get("images") if isinstance(meta.get("images"), list) else []
    for i, sc in enumerate(scenes):
        if i < len(prompts) and str(prompts[i]).strip():
            sc["visual"] = str(prompts[i]).strip()[:400]
        elif not sc.get("visual"):
            sc["visual"] = sc.get("voice") or sc.get("text")
        if i < len(images) and str(images[i]).strip():
            sc["image"] = str(images[i]).strip()[:40]
    title = str(meta.get("title") or parsed["title_hint"] or scenes[0].get("text") or "Untitled")[:80]
    caption = str(meta.get("caption") or (scenes[0].get("voice") or title))
    caption = scrub_text(caption, banned_list(profile))  # your scenes stay untouched; only the AI caption is filtered
    script = {"id": unique_id(title, existing_ids, today), "profile": profile["name"], "title": title,
              "mode": "script", "scenes": scenes, "caption": caption,
              "hashtags": [h for h in (meta.get("hashtags") or []) if isinstance(h, str)][:6]}
    if character:
        script["character"] = character
    validate_script(script, "your script")
    return script


def visual_prompts(script: dict, profile: dict) -> list[str]:
    """For scripts that have no 'visual' lines: one textless picture description per scene."""
    scenes = [{"n": i + 1, "voice": s.get("voice", ""), "headline": s.get("text", "")}
              for i, s in enumerate(script["scenes"])]
    prompt = f"""Write picture descriptions for an AI image generator, one per scene of this short video.
CHANNEL: {profile.get('description', profile['name'])}
SCENES: {json.dumps(scenes, ensure_ascii=False)}
Each description: one sentence showing the scene's idea with people, objects and setting.
Avoid objects that naturally contain writing (calendars, documents, screens, signs, charts).
The picture must contain NO text, words, numbers, signs or labels. No logos, brands or real people.
Return JSON: {{"prompts": ["...", "..."]}} with exactly {len(scenes)} strings, in order."""
    data = _extract_json(_ask(prompt))
    raw = data.get("prompts", data if isinstance(data, list) else [])
    out = []
    for p in raw:
        if isinstance(p, dict):  # some models return objects instead of strings
            p = p.get("prompt") or p.get("description") or next((v for v in p.values() if isinstance(v, str)), "")
        out.append(str(p).strip())
    print(f"  (AI wrote {sum(1 for o in out if o)} picture descriptions)")
    return (out + [""] * len(scenes))[:len(scenes)]
