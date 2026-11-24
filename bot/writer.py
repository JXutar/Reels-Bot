"""Turns a one-line idea into a full reel script using an AI writer.

Which AI:
  - ANTHROPIC_API_KEY set -> Claude (paid per use, best writing)
  - else GEMINI_API_KEY set -> Gemini free tier (the newest available "flash" model is picked automatically)
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


def available() -> str:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "Claude"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "Gemini"
    return ""


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "idea"


def _prompt(idea: str, profile: dict, example: dict | None) -> str:
    rules = "\n".join(f"- {r}" for r in profile.get("rules", []))
    ex = json.dumps(example, ensure_ascii=False, indent=1) if example else "(none)"
    idea_line = idea.strip() or "Choose a fresh, specific topic that fits this channel."
    return f"""You write scripts for short vertical videos (Instagram Reels).

CHANNEL: {profile.get('description', profile['name'])}
CHANNEL RULES:
{rules or '- (none)'}

IDEA FROM THE CREATOR: {idea_line}

Write ONE reel script as JSON with exactly these keys:
- "title": short title (max 60 chars)
- "background": 2-4 word stock-video search for the background (e.g. "rainy city street")
- "scenes": 5-8 objects, each with:
    - "voice": what the narrator says (natural spoken English, 1-3 sentences)
    - "text": a very short on-screen version of the line (max 10 words); wrap 1-2 key words in *asterisks*
    - "image": OPTIONAL 1-3 word stock-photo search for a pop-up picture (concrete, photographable things
      like "stone statue", "old village", "worried man"); include it on about half the scenes
  The first scene must hook the viewer in the first 2 seconds. The last scene must land the ending
  (a twist, punchline or takeaway) and can softly invite people to follow.
  Total narration: about 25-45 seconds when read aloud.
- "caption": Instagram caption, 2-4 short lines, may use 1-2 emojis
- "hashtags": 3-5 relevant hashtags

Return ONLY the JSON object, no explanations.

Here is an example of a previous script from this channel, for format and tone:
{ex}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- providers
def _claude(prompt: str) -> str:
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                      headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                               "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": CLAUDE_MODEL, "max_tokens": 3000,
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
    # try the best model first, then a fast "lite" one, then the rest
    lite = [m for m in models if "lite" in m]
    order = models[:1] + lite[:1] + [m for m in models[1:] if m not in lite[:1]]
    errors, started = [], time.time()
    for model in order[:5]:
        if time.time() - started > 360:  # stay well inside the workflow's time limit
            errors.append("gave up after 6 minutes")
            break
        thinking = _thinking_config(model)
        try:
            r = _gemini_call(key, model, prompt, thinking, timeout=75)
            if r.status_code == 400 and thinking and "thinking" in r.text.lower():
                r = _gemini_call(key, model, prompt, None, timeout=75)  # model doesn't accept that setting
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{model}: {type(e).__name__}")
            continue  # timeout / connection problem -> next model
        if r.status_code >= 400:
            errors.append(f"{model}: {data.get('error', {}).get('message', r.status_code)}")
            continue  # unavailable / over the free quota -> next model
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if text:
            print(f"  (written by {model})")
            return text
        errors.append(f"{model}: empty answer")
    raise RuntimeError("Gemini didn't answer: " + " | ".join(errors))


# ---------------------------------------------------------------- main entry
def write_script(idea: str, profile: dict, existing_ids: set[str], example: dict | None = None,
                 today: datetime | None = None) -> dict:
    provider = available()
    if not provider:
        raise RuntimeError("No AI writer configured. Add a GEMINI_API_KEY (free) or ANTHROPIC_API_KEY secret.")
    call = _claude if provider == "Claude" else _gemini
    prompt = _prompt(idea, profile, example)
    last_error = None
    for _attempt in range(2):
        try:
            data = _extract_json(call(prompt))
            today = today or datetime.now()
            base = f"{today:%Y-%m-%d}-{slugify(data.get('title') or idea)}"
            sid, n = base, 2
            while sid in existing_ids:
                sid, n = f"{base}-{n}", n + 1
            script = {"id": sid, "profile": profile["name"], "title": data["title"][:80],
                      "source_idea": idea.strip(), "background": data.get("background", ""),
                      "scenes": [], "caption": data.get("caption", ""), "hashtags": data.get("hashtags", [])[:6]}
            for i, sc in enumerate(data["scenes"][:10]):
                scene = {"voice": str(sc["voice"]).strip(), "text": str(sc.get("text") or sc["voice"])[:120]}
                if sc.get("image"):
                    scene["image"] = str(sc["image"])[:40]
                if i == 0:
                    scene["style"] = "hook"
                elif i == len(data["scenes"][:10]) - 1:
                    scene["style"] = "cta"
                script["scenes"].append(scene)
            validate_script(script, "AI draft")
            return script
        except SystemExit as e:  # validation failure
            last_error = e
            prompt += f"\n\nYour previous answer was invalid ({e}). Follow the format exactly."
        except (KeyError, ValueError, TypeError) as e:
            last_error = e
            prompt += "\n\nYour previous answer was not valid JSON with the required keys. Try again."
    raise RuntimeError(f"The AI's script wasn't usable: {last_error}")
