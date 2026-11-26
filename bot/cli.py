"""Command line entry point.  Run `python -m bot --help`.

  render --local [--file X]   make mp4s on your computer only (for previewing)
  render [--remake ID|all]    (GitHub) render new scripts, host them, send to Telegram for review
  tick                        (GitHub, every ~30 min) read Telegram, post what's due
  publish <id>                post one reel now
  refresh-token               renew Instagram tokens
  check                       validate scripts and test credentials

Telegram:  /idea  /script  /status  /post  /schedule  /unschedule  /remake  /help
Buttons on each new reel:  🚀 Post now · 🗓 Schedule · ❌ Reject
"""
from __future__ import annotations

import argparse
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, render, state as st
from .timeparse import describe, parse_when

NEEDS_RENDER = False  # set when /idea, /script or /remake creates work; the workflow then runs `render`
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
IDEA_ATTEMPTS = 3
OPEN = ("pending_review", "approved", "scheduled", "post_now", "failed")  # not posted, not rejected

HELP = ("/idea <your idea>  - AI writes + makes a reel\n"
        "/idea story: ...  /idea general: ...  - pick the profile\n"
        "/idea 60s: ...  - ask for a length (also: short, long)\n"
        "/script <full script>  - keeps every scene and line you wrote (also automatic when you paste "
        "SCENE / VO: / Text: / Visual: lines)\n"
        "/status  - what's waiting, scheduled, posted\n"
        "/post <id>  - post now\n"
        "/schedule <id> <when>  - e.g. /schedule deductible fri 9pm\n"
        "/unschedule <id>  - cancel a scheduled post (the reel is kept)\n"
        "/remake <id> or /remake all  - re-render reels that aren't posted yet\n"
        "Ids can be shortened to any unique part, e.g. 'deductible'.")


def now_local() -> datetime:
    return datetime.now(ZoneInfo(config.load_settings().get("timezone", "Asia/Kuala_Lumpur")))


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


def _notify(text: str) -> None:
    try:
        from . import telegram
        telegram.send_text(text)
    except Exception as e:
        print(f"(Telegram notify failed: {e}) {text}")


def find_item(state: dict, token: str, statuses=OPEN) -> tuple[str | None, str]:
    """Match a full or partial reel id. Returns (id, error message)."""
    token = token.strip().lower()
    if not token:
        return None, "Which reel? Send /status to see ids."
    if token in state["items"]:
        return token, ""
    hits = [i for i, it in state["items"].items() if token in i.lower() and it["status"] in statuses]
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, f"No reel matching '{token}'. Send /status to see ids."
    return None, f"'{token}' matches several reels: " + ", ".join(hits[:5]) + ". Use more of the id."


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
            msg_id = telegram.send_for_review(video, script["id"], script, caption)
            st.set_status(state, script["id"], "pending_review", profile=profile["name"],
                          telegram_message_id=msg_id, **hosted)
            print(f"Sent for review: {script['id']}")
        except Exception as e:
            traceback.print_exc()
            st.set_status(state, script["id"], "failed", profile=profile["name"], error=str(e)[:500])
            _notify(f"⚠️ Couldn't render {script['id']}: {e}")
        st.save(state)  # save after each so progress isn't lost


def remake(state: dict, target: str) -> list[str]:
    """Forget reels (not yet posted) so the next render makes them again with the current code/style."""
    from . import storage, telegram

    target = target.strip()
    if target != "all":
        item_id, _err = find_item(state, target, statuses=OPEN + ("rejected",))
        targets = [item_id] if item_id else []
    else:
        targets = [i for i, it in state["items"].items() if it["status"] in OPEN]
    removed = []
    for item_id in targets:
        item = state["items"][item_id]
        if item["status"] == "published":
            continue
        storage.delete(item.get("public_id", ""))
        if item.get("telegram_message_id"):
            telegram.mark_message(item["telegram_message_id"], "♻️ Replaced by a remake")
        del state["items"][item_id]
        removed.append(item_id)
    return removed


# ---------------------------------------------------------------- /idea and /script
def _resolve_profile(word: str) -> dict | None:
    word = word.lower().strip()
    for profile in config.active_profiles():
        names = [profile["name"].lower()] + [a.lower() for a in profile.get("aliases", [])]
        if word in names:
            return profile
    return None


def _length_word(word: str) -> int | None:
    w = word.lower().strip()
    if w == "short":
        return 30
    if w in ("medium", "normal"):
        return 45
    if w == "long":
        return 75
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds|m|min|mins|minute|minutes)?", w)
    if m:
        n = float(m.group(1))
        secs = n * 60 if (m.group(2) or "").startswith("m") else n
        return int(max(15, min(90, secs)))
    return None


def parse_request(text: str) -> tuple[str, dict, int | None, str]:
    """'/idea story 60s: a town where...' -> (mode, profile, seconds, body)."""
    command = "script" if text.lower().startswith("/script") else "idea"
    body = re.sub(r"^/(idea|script)\b", "", text, flags=re.I).strip()
    profile, seconds = None, None
    head, sep, rest = body.partition(":")
    words = head.split()
    if sep and 1 <= len(words) <= 3:
        found_profile, found_len, ok = None, None, True
        for w in words:
            p, l = _resolve_profile(w), _length_word(w)
            if p and not found_profile:
                found_profile = p
            elif l and not found_len:
                found_len = l
            else:
                ok = False
        if ok:
            profile, seconds, body = found_profile, found_len, rest.strip()
    elif not sep and 1 <= len(body.split()) <= 2:  # "/idea story" or "/idea story long" -> AI picks the topic
        words = body.split()
        p = _resolve_profile(words[0])
        l = _length_word(words[1]) if len(words) == 2 else None
        if p and (len(words) == 1 or l):
            profile, seconds, body = p, l, ""
    if profile is None:
        default = config.load_settings().get("idea_default_profile")
        profile = (_resolve_profile(default) if default else None) or config.active_profiles()[0]
    from . import writer
    mode = "script" if command == "script" or writer.looks_like_script(body) else "idea"
    return mode, profile, seconds, body


def handle_idea(text: str, state: dict | None = None, attempt: int = 1) -> None:
    """AI writes (idea mode) or formats (script mode) a script file into the queue.
    If the AI is slow or down, idea mode keeps the request and retries at the next checks."""
    global NEEDS_RENDER
    from . import writer

    mode, profile, seconds, body = parse_request(text)
    existing = {s["id"] for s, _p, _ in all_scripts()}

    if mode == "script":
        if attempt == 1:
            _notify(f"📜 Got your script ({profile['name']}). Keeping every scene and line as written…")
        try:
            script = writer.script_from_text(body, profile, existing)
        except Exception as e:
            traceback.print_exc()
            _notify(f"⚠️ Couldn't read that script: {e}")
            return
    else:
        if not writer.available():
            _notify("⚠️ /idea needs an AI writer. Add a GEMINI_API_KEY secret (free), then try again.")
            return
        if attempt == 1:
            what = f" about: {body}" if body else " (topic of my choice)"
            length = f", about {seconds}s" if seconds else ""
            _notify(f"📝 Writing a new {profile['name']} reel{length}{what}…")
        example = None
        examples = config.queued_scripts(profile["name"])
        if examples:
            ex = config.load_script(examples[-1])
            example = {"title": ex["title"], "caption": ex.get("caption", ""),
                       "scenes": [{k: s[k] for k in ("voice", "text") if k in s} for s in ex["scenes"]
                                  if s.get("style") != "cta"][:6]}
        try:
            script = writer.write_script(body, profile, existing, example, seconds=seconds)
        except Exception as e:
            traceback.print_exc()
            if state is not None and attempt < IDEA_ATTEMPTS:
                state.setdefault("idea_queue", []).append({"text": text, "attempts": attempt})
                if attempt == 1:
                    _notify("⏳ The AI is slow right now. I'll keep your idea and try again at the next check.")
            else:
                _notify(f"⚠️ Couldn't write that one after {attempt} tries: {e}\nSend the /idea again later.")
            return

    folder = config.QUEUE_DIR / profile["name"] / "ideas"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{script['id']}.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    NEEDS_RENDER = True
    n = len(script["scenes"])
    _notify(f"✍️ Script ready: \"{script['title']}\" ({n} scenes). Making the video now, "
            f"it'll arrive here in a few minutes.")


def retry_ideas(state: dict) -> None:
    """Ideas that failed at an earlier check get another go (before new messages are read)."""
    queue, state["idea_queue"] = state.get("idea_queue", []), []
    for item in queue:
        handle_idea(item["text"], state, attempt=item.get("attempts", 1) + 1)


# ---------------------------------------------------------------- publishing
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
    if item.get("telegram_message_id"):
        telegram.mark_message(item["telegram_message_id"], "✅ Posted")
    kind = "trial reel (non-followers only)" if ig.get("trial_reel", True) else "reel"
    extra = "\nCheck it in Instagram and tap 'Share to everyone' if it does well." \
        if ig.get("trial_reel", True) and ig.get("graduation") == "MANUAL" else ""
    _notify(f"✅ Posted '{script['title']}' as a {kind}.{extra}")
    return True


def cmd_publish(args) -> None:
    state = st.load()
    publish_one(state, args.id)
    st.save(state)


def _queue_post_now(state: dict, item_id: str) -> None:
    from . import telegram
    item = state["items"][item_id]
    st.set_status(state, item_id, "post_now")
    item.pop("scheduled_at", None)
    if item.get("telegram_message_id"):
        telegram.mark_message(item["telegram_message_id"], "🚀 Posting now…",
                              extra=[("❌ Cancel", f"reject|{item_id}")])


def _schedule(state: dict, item_id: str, when: datetime) -> None:
    from . import telegram
    item = state["items"][item_id]
    st.set_status(state, item_id, "scheduled", scheduled_at=when.isoformat())
    if item.get("telegram_message_id"):
        telegram.mark_message(item["telegram_message_id"], f"🗓 {describe(when)}",
                              extra=[("🚀 Post now instead", f"postnow|{item_id}"), ("❌ Cancel", f"reject|{item_id}")])


def _ask_when(state: dict, item_id: str) -> None:
    state["awaiting_time"] = {"id": item_id, "since": now_local().isoformat()}
    title = _scripts_by_id().get(item_id, ({"title": item_id}, None))[0]["title"]
    _notify(f"🗓 When should I post \"{title}\"?\nReply with a time, e.g.  tomorrow 8pm · fri 9pm · "
            f"28/11 20:00 · in 3 hours\n(or /cancel)")


def handle_updates(state: dict) -> None:
    global NEEDS_RENDER
    from . import storage, telegram

    allowed_chat = str(telegram.chat_id())
    updates = telegram.get_updates(state.get("telegram_offset", 0))
    ideas: list[dict] = []  # collected, then handled after reading everything (long scripts arrive split)
    for u in updates:
        state["telegram_offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != allowed_chat:
                continue
            action, _, item_id = cq.get("data", "").partition("|")
            item = state["items"].get(item_id)
            if not item or action not in ("postnow", "schedule", "reject", "approve"):
                telegram.answer_button(cq["id"], "Nothing to do")
                continue
            if item["status"] in ("published", "rejected"):
                telegram.answer_button(cq["id"], f"Already {item['status']}")
                continue
            if action in ("postnow", "approve"):  # old 'Approve' buttons behave like Post now
                _queue_post_now(state, item_id)
                telegram.answer_button(cq["id"], "Posting at this check")
            elif action == "schedule":
                telegram.answer_button(cq["id"], "When? Reply with a time")
                _ask_when(state, item_id)
            else:
                st.set_status(state, item_id, "rejected")
                item.pop("scheduled_at", None)
                storage.delete(item.get("public_id", ""))
                telegram.answer_button(cq["id"], "Rejected")
                telegram.mark_message(item["telegram_message_id"], "❌ Rejected")
            continue

        msg = u.get("message")
        if not msg or str(msg.get("chat", {}).get("id")) != allowed_chat:
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        low = text.lower()

        # a long script gets split by Telegram into several messages sent within seconds -> glue them back
        if not text.startswith("/") and ideas and msg.get("date", 0) - ideas[-1]["date"] <= 5:
            ideas[-1]["text"] += "\n" + text
            ideas[-1]["date"] = msg.get("date", 0)
            continue

        if low.startswith("/idea") or low.startswith("/script"):
            ideas.append({"text": text, "date": msg.get("date", 0)})
        elif low.startswith("/cancel"):
            state.pop("awaiting_time", None)
            _notify("OK, cancelled.")
        elif low.startswith("/remake"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                removed = remake(state, parts[1])
                if removed:
                    NEEDS_RENDER = True
                    _notify(f"♻️ Remaking {len(removed)} reel(s). New versions will arrive here shortly.")
                else:
                    _notify("Nothing to remake with that id (posted reels can't be remade). Send /status.")
            else:
                _notify("Usage: /remake <id>  or  /remake all")
        elif low.startswith("/unschedule"):
            parts = text.split(maxsplit=1)
            item_id, err = find_item(state, parts[1] if len(parts) == 2 else "", statuses=("scheduled", "post_now"))
            if item_id:
                st.set_status(state, item_id, "approved")
                state["items"][item_id].pop("scheduled_at", None)
                it = state["items"][item_id]
                if it.get("telegram_message_id"):
                    telegram.mark_message(it["telegram_message_id"], "⏸ Not scheduled",
                                          extra=[("🚀 Post now", f"postnow|{item_id}"),
                                                 ("🗓 Schedule", f"schedule|{item_id}")])
                _notify(f"⏸ {item_id} won't post until you /post or /schedule it.")
            else:
                _notify(err)
        elif low.startswith("/schedule"):
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                _notify("Usage: /schedule <id> <when>, e.g. /schedule deductible fri 9pm")
                continue
            item_id, err = find_item(state, parts[1])
            if not item_id:
                _notify(err)
                continue
            if len(parts) == 2:
                _ask_when(state, item_id)
                continue
            when = parse_when(parts[2], now_local())
            if not when:
                _notify(f"I didn't understand '{parts[2]}'. Try: tomorrow 8pm, fri 9pm, 28/11 20:00, in 3 hours.")
            elif when < now_local() - timedelta(minutes=5):
                _notify(f"That's in the past ({describe(when)}). Pick a later time.")
            else:
                _schedule(state, item_id, when)
                _notify(f"🗓 {item_id} scheduled for {describe(when)}.")
        elif low.startswith("/post"):
            parts = text.split(maxsplit=1)
            item_id, err = find_item(state, parts[1] if len(parts) == 2 else "")
            if item_id:
                _queue_post_now(state, item_id)
                _notify(f"🚀 Posting {item_id} now.")
            else:
                _notify(err)
        elif low.startswith("/status"):
            _notify(status_report(state))
        elif low.startswith("/start") or low.startswith("/help"):
            _notify(f"Hi! Your chat id is {msg['chat']['id']}.\n\n" + HELP)
        elif not text.startswith("/") and state.get("awaiting_time"):
            item_id = state["awaiting_time"]["id"]
            when = parse_when(text, now_local())
            if not when:
                _notify(f"I didn't understand '{text}'. Try: tomorrow 8pm, fri 9pm, 28/11 20:00, in 3 hours "
                        f"(or /cancel).")
            elif when < now_local() - timedelta(minutes=5):
                _notify(f"That's in the past ({describe(when)}). Pick a later time (or /cancel).")
            elif item_id in state["items"]:
                state.pop("awaiting_time", None)
                _schedule(state, item_id, when)
                _notify(f"🗓 Scheduled for {describe(when)} ✅")
        elif not text.startswith("/"):
            _notify("Send /help to see what I can do.")

    for idea in ideas:
        handle_idea(idea["text"], state)


def status_report(state: dict) -> str:
    items = state["items"]

    def by(s):
        return [i for i, it in items.items() if it["status"] == s]

    lines = []
    if by("pending_review"):
        lines.append(f"👀 Waiting for you ({len(by('pending_review'))}):\n  " + "\n  ".join(by("pending_review")))
    if by("post_now"):
        lines.append("🚀 Posting at this check:\n  " + "\n  ".join(by("post_now")))
    sched = sorted(by("scheduled"), key=lambda i: items[i].get("scheduled_at", ""))
    if sched:
        lines.append("🗓 Scheduled:\n  " + "\n  ".join(
            f"{describe(datetime.fromisoformat(items[i]['scheduled_at']))}  {i}" for i in sched))
    if by("approved"):
        lines.append("⏸ Ready, not scheduled (use /post or /schedule):\n  " + "\n  ".join(by("approved")))
    if by("failed"):
        lines.append("⚠️ Failed:\n  " + "\n  ".join(by("failed")))
    lines.append(f"✅ Posted: {len(by('published'))}   ❌ Rejected: {len(by('rejected'))}")
    return "\n\n".join(lines)


# ---- optional automatic weekly posting (profile: posting: auto)
def latest_slot(now: datetime, sch: dict) -> datetime | None:
    for back in range(0, 8):
        day = now - timedelta(days=back)
        if DAYS[day.weekday()] in sch.get("days", []):
            slot = day.replace(hour=sch.get("hour", 20), minute=0, second=0, microsecond=0)
            if slot <= now:
                return slot
    return None


def _posted_in_slot(last, slot: datetime) -> int:
    if isinstance(last, str):
        last = {"date": last, "count": 1}
    if not isinstance(last, dict):
        return 0
    if last.get("slot"):
        return last.get("count", 0) if last["slot"] == slot.strftime("%Y-%m-%dT%H:%M") else 0
    return last.get("count", 0) if last.get("date") == slot.strftime("%Y-%m-%d") else 0


def due_profiles(state: dict, now: datetime) -> list[tuple[dict, datetime]]:
    due = []
    for profile in config.active_profiles():
        if profile.get("posting", "manual") != "auto":
            continue
        sch = profile.get("schedule", {})
        slot = latest_slot(now, sch)
        if not slot or now - slot > timedelta(hours=sch.get("catch_up_hours", 24)):
            continue
        if _posted_in_slot(state.get("last_publish", {}).get(profile["name"]), slot) < sch.get("max_per_day", 1):
            due.append((profile, slot))
    return due


def due_items(state: dict, now: datetime) -> list[str]:
    """Reels to post at this check: 'Post now' ones, then scheduled ones whose time has come."""
    items = state["items"]
    now_ids = [i for i, it in items.items() if it["status"] == "post_now"]
    sched = [i for i, it in items.items() if it["status"] == "scheduled" and it.get("scheduled_at")
             and datetime.fromisoformat(it["scheduled_at"]) <= now]
    return now_ids + sorted(sched, key=lambda i: items[i]["scheduled_at"])


def cmd_tick(args) -> None:
    state = st.load()
    try:
        retry_ideas(state)
        handle_updates(state)
    finally:
        st.save(state)

    now = now_local()
    for item_id in due_items(state, now):
        publish_one(state, item_id)
        st.save(state)

    for profile, slot in due_profiles(state, now):  # only profiles with posting: auto
        approved = [(i, it) for i, it in state["items"].items()
                    if it.get("profile") == profile["name"] and it["status"] == "approved"]
        if not approved:
            continue
        approved.sort(key=lambda x: x[1]["history"][-1]["at"])
        if publish_one(state, approved[0][0]):
            count = _posted_in_slot(state.setdefault("last_publish", {}).get(profile["name"]), slot)
            state["last_publish"][profile["name"]] = {"slot": slot.strftime("%Y-%m-%dT%H:%M"),
                                                      "date": slot.strftime("%Y-%m-%d"), "count": count + 1}
        st.save(state)

    sched = sum(1 for it in state["items"].values() if it["status"] == "scheduled")
    print(f"Tick done at {now:%Y-%m-%d %H:%M}. Scheduled: {sched}")
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
        from . import instagram, media, telegram, writer
        seen = set()
        for profile in config.active_profiles():
            ig = profile["instagram"]
            if ig["token_secret"] in seen:
                continue
            seen.add(ig["token_secret"])
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
        telegram.send_text("👋 Reels bot connected. Send /help for commands.")
        print("Telegram OK: test message sent.")
        print(f"AI writer for /idea: {writer.available() or 'none (add GEMINI_API_KEY to use /idea)'}")
        if media.ai_available():
            try:
                media.ai_image("a simple flat illustration of a sunrise", Path("/tmp/cf_test.png"), seed=1, steps=4)
                print("AI pictures (Cloudflare): OK")
            except Exception as e:
                print(f"AI pictures (Cloudflare): FAILED - {e}")
        else:
            print("AI pictures: not set up (add CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN); stock photos used")


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
    c.add_argument("--online", action="store_true", help="also test Instagram, Telegram, AI writer and pictures")
    c.set_defaults(func=cmd_check)
    args = parser.parse_args()
    args.func(args)
