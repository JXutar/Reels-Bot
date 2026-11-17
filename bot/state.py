"""A small JSON file that remembers what happened to every reel.

Statuses: pending_review -> approved -> published
                         -> rejected
          (any) -> failed  (with the error saved, so it can be retried)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import STATE_FILE


def load() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"telegram_offset": 0, "items": {}, "last_publish": {}}


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def set_status(state: dict, item_id: str, status: str, **fields) -> dict:
    item = state["items"].setdefault(item_id, {"history": []})
    item["status"] = status
    item.update(fields)
    item["history"].append({"status": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return item
