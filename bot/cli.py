"""Command line entry point.  Run `python -m bot --help`.

  render --local [--file X]   make mp4s on your computer only (for previewing)
  render                      (GitHub) render new scripts, host them, send to Telegram for review
  tick                        (GitHub, hourly) read your Telegram taps, post on schedule
  publish <id>                post one approved reel now
  refresh-token               renew Instagram tokens (monthly workflow)
  check                       validate scripts and test credentials
"""
from __future__ import annotations

import argparse
import os
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, render, state as st

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_caption(script: dict, profile: dict) -> str:
    cap = profile.get("caption", {})
    tags = []
    for t in script.get("hashtags", []) + cap.get("default_hashtags", []):
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    parts = [script["caption"].strip(), cap.get("footer", "").strip(), " ".join(tags[:8])]
    return "\n\n".join(p for p in parts if p)


def all_scripts() -> list[tuple[dict, dict, Path]]:
    found = []
    for profile in config.active_profiles():
        for path in config.queued_scripts(profile["name"]):
            found.append((config.load_script(path), profile, path))
    return found


# ---------------------------------------------------------------- render
def cmd_render(args) -> None:
    if args.local:
        targets = [Path(args.file)] if args.file else None
        for script, profile, path in all_scripts():
            if targets and path.resolve() not in [t.resolve() for t in targets]:
                continue
            out = render.render(script, profile, dummy_tts=args.dummy_tts)
            print(f"Rendered {out}")
        return

    from . import storage, telegram

    state = st.load()
    for script, profile, _path in all_scripts():
        if script["id"] in state["items"]:
            continue
        try:
            video = render.render(script, profile, dummy_tts=bool(os.environ.get("DUMMY_TTS")))
            hosted = storage.upload(video, script["id"])
            caption = build_caption(script, profile)
            msg_id = telegram.send_for_review(video, script["id"], script, caption)
            st.set_status(state, script["id"], "pending_review", profile=profile["name"],
                          telegram_message_id=msg_id, **hosted)
            print(f"Sent for review: {script['id']}")
        except Exception as e:
            traceback.print_exc()
            st.set_status(state, script["id"], "failed", profile=profile["name"], error=str(e)[:500])
            _notify(f"⚠️ Couldn't render {script['id']}: {e}")
        st.save(state)  # save after each so progress isn't lost


# ---------------------------------------------------------------- publish
def _scripts_by_id() -> dict:
    return {s["id"]: (s, p) for s, p, _ in all_scripts()}


def publish_one(state: dict, item_id: str) -> bool:
    from . import instagram, storage, telegram

    found = _scripts_by_id().get(item_id)
    item = state["items"].get(item_id)
    if not found or not item:
        _notify(f"Unknown reel id: {item_id}")
        return False
    script, profile = found
    ig = profile["instagram"]
    try:
        media_id = instagram.publish_reel(
            user_id=config.env(ig["user_id_secret"]), token=config.env(ig["token_secret"]),
            video_url=item["video_url"], caption=build_caption(script, profile),
            trial=ig.get("trial_reel", True), graduation=ig.get("graduation", "MANUAL"))
    except Exception as e:
        traceback.print_exc()
        st.set_status(state, item_id, "failed", error=str(e)[:500])
        _notify(f"⚠️ Posting failed for {item_id}:\n{e}\n\nFix the issue, then send /post {item_id} to retry.")
        return False
    st.set_status(state, item_id, "published", media_id=media_id)
    storage.delete(item.get("public_id", ""))
    kind = "trial reel (non-followers only)" if ig.get("trial_reel", True) else "reel"
    extra = "\nCheck it in Instagram and tap 'Share to everyone' if it does well." \
        if ig.get("trial_reel", True) and ig.get("graduation") == "MANUAL" else ""
    _notify(f"✅ Posted '{script['title']}' as a {kind}.{extra}")
    return True


def cmd_publish(args) -> None:
    state = st.load()
    publish_one(state, args.id)
    st.save(state)


# ---------------------------------------------------------------- tick
def _notify(text: str) -> None:
    try:
        from . import telegram
        telegram.send_text(text)
    except Exception as e:
        print(f"(Telegram notify failed: {e}) {text}")


def handle_updates(state: dict) -> None:
    from . import storage, telegram

    allowed_chat = str(telegram.chat_id())
    updates = telegram.get_updates(state.get("telegram_offset", 0))
    for u in updates:
        state["telegram_offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != allowed_chat:
                continue
            action, _, item_id = cq.get("data", "").partition("|")
            item = state["items"].get(item_id)
            if action not in ("approve", "reject") or not item:
                telegram.answer_button(cq["id"], "Nothing to do")
                continue
            if item["status"] != "pending_review":
                telegram.answer_button(cq["id"], f"Already {item['status']}")
                continue
            if action == "approve":
                st.set_status(state, item_id, "approved")
                telegram.answer_button(cq["id"], "Approved, it'll post on the next slot")
                telegram.mark_message(item["telegram_message_id"], "✅ Approved")
            else:
                st.set_status(state, item_id, "rejected")
                storage.delete(item.get("public_id", ""))
                telegram.answer_button(cq["id"], "Rejected")
                telegram.mark_message(item["telegram_message_id"], "❌ Rejected")
        elif "message" in u:
            msg = u["message"]
            if str(msg.get("chat", {}).get("id")) != allowed_chat:
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/status"):
                _notify(status_report(state))
            elif text.startswith("/post"):
                parts = text.split()
                if len(parts) == 2 and state["items"].get(parts[1], {}).get("status") in ("approved", "failed"):
                    publish_one(state, parts[1])
                else:
                    _notify("Usage: /post <id>  (the reel must be approved). Send /status to see ids.")
            elif text.startswith("/start") or text.startswith("/help"):
                _notify(f"Hi! Your chat id is {msg['chat']['id']}.\nCommands: /status, /post <id>")


def status_report(state: dict) -> str:
    groups: dict[str, list[str]] = {}
    for item_id, item in state["items"].items():
        groups.setdefault(item["status"], []).append(item_id)
    order = ["pending_review", "approved", "failed", "published", "rejected"]
    lines = []
    for s in order:
        ids = groups.get(s, [])
        if ids and s not in ("published", "rejected"):
            lines.append(f"{s} ({len(ids)}):\n  " + "\n  ".join(ids))
        elif ids:
            lines.append(f"{s}: {len(ids)}")
    return "\n".join(lines) or "Nothing in the queue yet."


def due_profiles(state: dict, now: datetime) -> list[dict]:
    due = []
    for profile in config.active_profiles():
        sch = profile.get("schedule", {})
        today = now.strftime("%Y-%m-%d")
        last = state.get("last_publish", {}).get(profile["name"]) or {}
        if isinstance(last, str):  # older state format
            last = {"date": last, "count": 1}
        posted_today = last.get("count", 0) if last.get("date") == today else 0
        if DAYS[now.weekday()] in sch.get("days", []) and now.hour >= sch.get("hour", 20) \
                and posted_today < sch.get("max_per_day", 1):
            due.append(profile)
    return due


def cmd_tick(args) -> None:
    state = st.load()
    try:
        handle_updates(state)
    finally:
        st.save(state)

    tz = ZoneInfo(config.load_settings().get("timezone", "Asia/Kuala_Lumpur"))
    now = datetime.now(tz)
    for profile in due_profiles(state, now):
        approved = [(i, it) for i, it in state["items"].items()
                    if it.get("profile") == profile["name"] and it["status"] == "approved"]
        if not approved:
            continue
        approved.sort(key=lambda x: x[1]["history"][-1]["at"])  # oldest approval first
        if publish_one(state, approved[0][0]):
            today = now.strftime("%Y-%m-%d")
            last = state.setdefault("last_publish", {}).get(profile["name"]) or {}
            count = last.get("count", 0) if isinstance(last, dict) and last.get("date") == today else 0
            state["last_publish"][profile["name"]] = {"date": today, "count": count + 1}
        st.save(state)

    pending = [i for i, it in state["items"].items() if it["status"] == "approved"]
    print(f"Tick done at {now:%Y-%m-%d %H:%M}. Approved and waiting: {len(pending)}")


# ---------------------------------------------------------------- tokens / checks
def cmd_refresh_token(args) -> None:
    from . import instagram

    out_dir = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "new_tokens"
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    for profile in config.active_profiles():
        name = profile["instagram"]["token_secret"]
        if name in seen:
            continue
        seen.add(name)
        new = instagram.refresh_token(config.env(name))
        (out_dir / name).write_text(new["access_token"])
        days = int(new.get("expires_in", 0)) // 86400
        print(f"Refreshed {name}; valid for ~{days} more days")


def cmd_check(args) -> None:
    scripts = all_scripts()
    print(f"{len(scripts)} script(s) valid.")
    if args.online:
        from . import instagram, telegram
        for profile in config.active_profiles():
            ig = profile["instagram"]
            token, uid = config.env(ig["token_secret"]), config.env(ig["user_id_secret"])
            print(f"[{profile['name']}] token looks like: {token[:4]}... ({len(token)} characters)")
            if " " in token or len(token) < 100:
                print("  ⚠️ That looks too short or has spaces. Copy the whole token again from the Meta page.")
            me = instagram.whoami(token, uid)
            print(f"[{profile['name']}] Instagram OK: @{me.get('username')} ({me.get('account_type')}), "
                  f"user_id {me.get('user_id')}")
            if str(me.get("user_id")) != uid.strip():
                print(f"  ⚠️ IG_USER_ID secret is {uid}, but the token belongs to user_id {me.get('user_id')}. "
                      f"Set IG_USER_ID to {me.get('user_id')}.")
        telegram.send_text("👋 Reels bot connected. You'll get reels to review here.")
        print("Telegram OK: test message sent.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="bot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--local", action="store_true", help="only make the mp4s, no upload or Telegram")
    r.add_argument("--file", help="render just this script file (with --local)")
    r.add_argument("--dummy-tts", action="store_true", help="silent audio (offline testing)")
    r.set_defaults(func=cmd_render)
    sub.add_parser("tick").set_defaults(func=cmd_tick)
    p = sub.add_parser("publish")
    p.add_argument("id")
    p.set_defaults(func=cmd_publish)
    sub.add_parser("refresh-token").set_defaults(func=cmd_refresh_token)
    c = sub.add_parser("check")
    c.add_argument("--online", action="store_true", help="also test Instagram + Telegram credentials")
    c.set_defaults(func=cmd_check)
    args = parser.parse_args()
    args.func(args)
