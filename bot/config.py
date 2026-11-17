"""Loading settings, profiles and scripts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
QUEUE_DIR = ROOT / "content" / "queue"
ASSETS_DIR = ROOT / "content" / "assets"
OUT_DIR = ROOT / "out"
STATE_FILE = ROOT / "state" / "state.json"


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_profile(name: str) -> dict:
    path = CONFIG_DIR / "profiles" / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"Profile not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def active_profiles() -> list[dict]:
    return [load_profile(n) for n in load_settings().get("active_profiles", [])]


def load_script(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        script = json.load(f)
    validate_script(script, path)
    return script


def validate_script(script: dict, path: Path | str = "") -> None:
    problems = []
    for key in ("id", "profile", "title", "scenes", "caption"):
        if key not in script:
            problems.append(f"missing '{key}'")
    for i, scene in enumerate(script.get("scenes", [])):
        if not scene.get("text") or not scene.get("voice"):
            problems.append(f"scene {i + 1} needs both 'text' and 'voice'")
    if not 2 <= len(script.get("scenes", [])) <= 12:
        problems.append("needs 2-12 scenes")
    if problems:
        raise SystemExit(f"Invalid script {path}: " + "; ".join(problems))


def queued_scripts(profile_name: str) -> list[Path]:
    folder = QUEUE_DIR / profile_name
    return sorted(folder.glob("**/*.json")) if folder.exists() else []


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SystemExit(f"Missing environment variable / GitHub secret: {name}")
    return value
