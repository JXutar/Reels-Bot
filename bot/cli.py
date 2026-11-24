"""Command line entry point.  Run `python -m bot --help`.

  render --local [--file X]   make mp4s on your computer only (for previewing)
  render [--remake ID|all]    (GitHub) render new scripts, host them, send to Telegram for review
  tick                        (GitHub, hourly) read Telegram (taps, /idea, /remake), post on schedule
  publish <id>                post one approved reel now
  refresh-token               renew Instagram tokens (monthly workflow)
  check                       validate scripts and test credentials
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, render, state as st

NEEDS_RENDER = False  # set when /idea or /remake creates work; the workflow then runs `render`

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
    if getattr(args, "remake", ""):
        removed = remake(state, args.remake)
        print(f"Remaking: {', '.join(removed) or 'nothing matched'}")
        st.save(state)
    for script, profile, _path in all_scripts():
        if script["id"] in state["items"]:
            continue
        try:
            video = render.render(script, profile, dummy_tts=bool(os.environ.get("DUMMY_TTS")))
            hosted = storage.upload(video, script["id"])
            caption = build_caption(script, profile)
            auto = profile.get("review", {}).get("auto_approve", False)
            msg_id = telegram.send_for_review(video, script["id"], script, caption, auto_approved=auto)
            st.set_status(state, script["id"], "approved" if auto else "pending_review", profile=profile["name"],
                          telegram_message_id=msg_id, **hosted)
            print(f"{'Auto-approved' if auto else 'Sent for review'}: {script['id']}")
        except Exception as e:
            traceback.print_exc()
            st.set_status(state, script["id"], "failed", profile=profile["name"], error=str(e)[:500])
            _notify(f"⚠️ Couldn't render {script['id']}: {e}")
        st.save(state)  # save after each so progress isn't lost


def remake(state: dict, target: str) -> list[str]:
    """Forget reels (not yet posted) so the next render makes them again with the current code/style."""
    from . import storage, telegram

    target = target.strip()
    removed = []
    for item_id in list(state["items"]):
        item = state["items"][item_id]
        if item["status"] == "published":
            continue
        if target != "all" and item_id != target:
            continue
        storage.delete(item.get("public_id", ""))
        if item.get("telegram_message_id"):
            telegram.mark_message(item["telegram_message_id"], "♻️ Replaced by a remake")
        del state["items"][item_id]
        removed.append(item_id)
    return removed


def _resolve_profile(word: str) -> dict | None:
    word = word.lower().strip()
    for profile in config.active_profiles():
        names = [profile["name"].lower()] + [a.lower() for a in profile.get("aliases", [])]
        if word in names:
            return profile
    return None


def handle_idea(text: str) -> None:
    """'/idea story: a town where...' -> AI writes a script file into the queue."""
    global NEEDS_RENDER
    from . import writer

    body = text[len("/idea"):].strip()
    profile = None
    head, sep, rest = body.partition(":")
    if sep and len(head.split()) == 1:
        profile = _resolve_profile(head)
        if profile:
            body = rest.strip()
    if profile is None and body and len(body.split()) == 1:
        maybe = _resolve_profile(body)  # "/idea story" -> AI picks the topic
        if maybe:
            profile, body = maybe, ""
    if profile is None:
        default = config.load_settings().get("idea_default_profile")
        profile = _resolve_profile(default) if default else None
        profile = profile or config.active_profiles()[0]

    if not writer.available():
        _notify("⚠️ /idea needs an AI writer. Add a GEMINI_API_KEY secret (free), then try again.")
        return
    _notify(f"📝 Writing a new {profile['name']} reel" + (f" about: {body}" if body else " (topic of my choice)") + "…")
    existing = {s["id"] for s, _p, _ in all_scripts()}
    examples = config.queued_scripts(profile["name"])
    example = None
    if examples:
        example = config.load_script(examples[-1])
        example = {k: example[k] for k in ("title", "scenes", "caption", "hashtags") if k in example}
    try:
        script = writer.write_script(body, profile, existing, example)
    except Exception as e:
        traceback.print_exc()
        _notify(f"⚠️ Couldn't write that one: {e}")
        return
    folder = config.QUEUE_DIR / profile["name"] / "ideas"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{script['id']}.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    NEEDS_RENDER = True
    _notify(f"✍️ Script ready: \"{script['title']}\". Making the video now, it'll arrive here in a few minutes.")


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
    global NEEDS_RENDER
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
            allowed = ("pending_review",) if action == "approve" else ("pending_review", "approved")
            if item["status"] not in allowed:
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
            if text.startswith("/idea"):
                handle_idea(text)
            elif text.startswith("/remake"):
                parts = text.split()
                if len(parts) == 2:
                    removed = remake(state, parts[1])
                    if removed:
                        NEEDS_RENDER = True
                        _notify(f"♻️ Remaking {len(removed)} reel(s). New versions will arrive here shortly.")
                    else:
                        _notify("Nothing to remake with that id (posted reels can't be remade). Send /status.")
                else:
                    _notify("Usage: /remake <id>  or  /remake all")
            elif text.startswith("/status"):
                _notify(status_report(state))
            elif text.startswith("/post"):
                parts = text.split()
                if len(parts) == 2 and state["items"].get(parts[1], {}).get("status") in ("approved", "failed"):
                    publish_one(state, parts[1])
                else:
                    _notify("Usage: /post <id>  (the reel must be approved). Send /status to see ids.")
            elif text.startswith("/start") or text.startswith("/help"):
                _notify(f"Hi! Your chat id is {msg['chat']['id']}.\n\n"
                        "/idea <your idea>  - I write + make a reel (e.g. /idea story: a town where...)\n"
                        "/idea story  - I pick the topic\n"
                        "/status  - what's waiting, approved, posted\n"
                        "/post <id>  - post an approved reel now\n"
                        "/remake <id> or /remake all  - re-render reels that aren't posted yet")


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


def latest_slot(now: datetime, sch: dict) -> datetime | None:
    """Most recent posting slot (day + hour) at or before now."""
    for back in range(0, 8):
        day = now - timedelta(days=back)
        if DAYS[day.weekday()] in sch.get("days", []):
            slot = day.replace(hour=sch.get("hour", 20), minute=0, second=0, microsecond=0)
            if slot <= now:
                return slot
    return None


def _posted_in_slot(last, slot: datetime) -> int:
    if isinstance(last, str):  # oldest format: just a date
        last = {"date": last, "count": 1}
    if not isinstance(last, dict):
        return 0
    if last.get("slot"):
        return last.get("count", 0) if last["slot"] == slot.strftime("%Y-%m-%dT%H:%M") else 0
    return last.get("count", 0) if last.get("date") == slot.strftime("%Y-%m-%d") else 0


def due_profiles(state: dict, now: datetime) -> list[tuple[dict, datetime]]:
    """A profile is due if its latest slot hasn't had its posts yet and we're within the catch-up window
    (GitHub sometimes runs the hourly check many hours late)."""
    due = []
    for profile in config.active_profiles():
        sch = profile.get("schedule", {})
        slot = latest_slot(now, sch)
        if not slot or now - slot > timedelta(hours=sch.get("catch_up_hours", 24)):
            continue
        last = state.get("last_publish", {}).get(profile["name"])
        if _posted_in_slot(last, slot) < sch.get("max_per_day", 1):
            due.append((profile, slot))
    return due


def cmd_tick(args) -> None:
    state = st.load()
    try:
        handle_updates(state)
    finally:
        st.save(state)

    tz = ZoneInfo(config.load_settings().get("timezone", "Asia/Kuala_Lumpur"))
    now = datetime.now(tz)
    for profile, slot in due_profiles(state, now):
        approved = [(i, it) for i, it in state["items"].items()
                    if it.get("profile") == profile["name"] and it["status"] == "approved"]
        if not approved:
            continue
        approved.sort(key=lambda x: x[1]["history"][-1]["at"])  # oldest approval first
        if publish_one(state, approved[0][0]):
            count = _posted_in_slot(state.setdefault("last_publish", {}).get(profile["name"]), slot)
            state["last_publish"][profile["name"]] = {"slot": slot.strftime("%Y-%m-%dT%H:%M"),
                                                      "date": slot.strftime("%Y-%m-%d"), "count": count + 1}
        st.save(state)

    pending = [i for i, it in state["items"].items() if it["status"] == "approved"]
    print(f"Tick done at {now:%Y-%m-%d %H:%M}. Approved and waiting: {len(pending)}")
    if NEEDS_RENDER:
        print("New work queued -> rendering next.")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("render=true\n")


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
        telegram.send_text("👋 Reels bot connected. You'll get reels to review here. Send /help for commands.")
        print("Telegram OK: test message sent.")
        from . import writer
        print(f"AI writer for /idea: {writer.available() or 'none (add GEMINI_API_KEY to use /idea)'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="bot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--local", action="store_true", help="only make the mp4s, no upload or Telegram")
    r.add_argument("--file", help="render just this script file (with --local)")
    r.add_argument("--dummy-tts", action="store_true", help="silent audio (offline testing)")
    r.add_argument("--remake", default="", help="re-render this reel id (or 'all') that isn't posted yet")
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
