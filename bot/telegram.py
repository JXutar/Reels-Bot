"""Review on your phone: the bot sends each new reel with Approve / Reject buttons.

Each new reel arrives with buttons:  🚀 Post now · 🗓 Schedule · ❌ Reject
Commands: /idea /script /status /post /schedule /unschedule /remake /help (see cli.HELP)
"""
from __future__ import annotations

from pathlib import Path

import requests

from .config import env

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, files=None, **params):
    url = API.format(token=env("TELEGRAM_BOT_TOKEN"), method=method)
    r = requests.post(url, data=params if files else None, json=None if files else params,
                      files=files, timeout=300)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def chat_id() -> str:
    return env("TELEGRAM_CHAT_ID")


def send_text(text: str) -> None:
    _call("sendMessage", chat_id=chat_id(), text=text[:4000])


def send_for_review(video: Path, item_id: str, script: dict, caption_text: str) -> int:
    import json

    keyboard = {"inline_keyboard": [[{"text": "🚀 Post now", "callback_data": f"postnow|{item_id}"},
                                     {"text": "🗓 Schedule", "callback_data": f"schedule|{item_id}"},
                                     {"text": "❌ Reject", "callback_data": f"reject|{item_id}"}]]}
    n = len(script.get("scenes", []))
    preview = f"🎬 New reel: {script['title']}\nID: {item_id}  ·  {n} scenes\n\n{caption_text}"
    with open(video, "rb") as f:
        msg = _call("sendVideo", files={"video": f}, chat_id=chat_id(), caption=preview[:1000],
                    supports_streaming="true", reply_markup=json.dumps(keyboard))
    return msg["message_id"]


def get_updates(offset: int) -> list[dict]:
    return _call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])


def answer_button(callback_id: str, text: str) -> None:
    try:
        _call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except RuntimeError:
        pass  # old buttons can't be answered; harmless


def mark_message(message_id: int, note: str, extra: list[tuple[str, str]] | None = None) -> None:
    """Replace a reel's buttons with a status label (plus optional action buttons)."""
    rows = [[{"text": note, "callback_data": "noop|-"}]]
    if extra:
        rows.append([{"text": t, "callback_data": d} for t, d in extra])
    try:
        _call("editMessageReplyMarkup", chat_id=chat_id(), message_id=message_id,
              reply_markup={"inline_keyboard": rows})
    except RuntimeError:
        pass
