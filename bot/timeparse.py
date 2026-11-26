"""Turns plain-English times into a datetime, for scheduling posts.

Understands things like:
  now · in 30 min · in 3 hours · in 2 days
  8pm · 8:30pm · 20:00 · 8.30pm
  today 9pm · tonight · tomorrow 8pm · tmr 10am
  fri 9pm · friday · next mon 8am
  28/11 20:00 · 28/11/2026 8pm · 28 nov 9pm · nov 28 9pm
A day with no time means 8pm. A time with no day means the next time that clock time comes round.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

DEFAULT_HOUR = 20
WEEKDAYS = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _time_of_day(text: str):
    """Find a clock time; returns ((hour, minute), remaining_text) or (None, text)."""
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", text)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not 1 <= h <= 12 or mi > 59:
            return None, text
        h = h % 12 + (12 if ap == "pm" else 0)
        return (h, mi), text[:m.start()] + " " + text[m.end():]
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
    if m:
        return (int(m.group(1)), int(m.group(2))), text[:m.start()] + " " + text[m.end():]
    if re.search(r"\bnoon\b", text):
        return (12, 0), re.sub(r"\bnoon\b", " ", text)
    if re.search(r"\bmidnight\b", text):
        return (0, 0), re.sub(r"\bmidnight\b", " ", text)
    return None, text


def parse_when(text: str, now: datetime) -> datetime | None:
    """Returns a timezone-aware datetime (same tz as `now`), or None if it can't be understood."""
    t = " " + text.lower().strip().replace(",", " ") + " "
    if re.search(r"\b(now|asap|immediately)\b", t):
        return now

    m = re.search(r"\bin\s+(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b", t)
    if m:
        n, unit = float(m.group(1)), m.group(2)
        delta = timedelta(minutes=n) if unit.startswith("m") else \
            timedelta(hours=n) if unit.startswith("h") else timedelta(days=n)
        return now + delta

    tod, rest = _time_of_day(t)
    day = None

    # explicit dates: 28/11, 28/11/2026, 28-11
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", rest)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        y = y + 2000 if y < 100 else y
        try:
            day = now.replace(year=y, month=mo, day=d)
        except ValueError:
            return None
        if not m.group(3) and day.date() < now.date():
            day = day.replace(year=y + 1)
    else:
        # 28 nov / nov 28
        m = re.search(r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", rest) or \
            re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*(\d{1,2})\b", rest)
        if m:
            a, b = m.group(1), m.group(2)
            d, mon = (int(a), b) if a.isdigit() else (int(b), a)
            try:
                day = now.replace(month=MONTHS[mon[:3]], day=d)
            except ValueError:
                return None
            if day.date() < now.date():
                day = day.replace(year=now.year + 1)

    if day is None:
        if re.search(r"\b(today|tonight)\b", rest):
            day = now
            if tod is None and "tonight" in rest:
                tod = (21, 0)
        elif re.search(r"\b(tomorrow|tmr|tmrw|tomoro|tomorow)\b", rest):
            day = now + timedelta(days=1)
        else:
            for word, idx in sorted(WEEKDAYS.items(), key=lambda x: -len(x[0])):
                if re.search(rf"\b{word}[a-z]*\b", rest):
                    ahead = (idx - now.weekday()) % 7
                    if re.search(r"\bnext\b", rest) and ahead == 0:
                        ahead = 7
                    day = now + timedelta(days=ahead)
                    if ahead == 0:
                        h, mi = tod or (DEFAULT_HOUR, 0)
                        if now.replace(hour=h, minute=mi, second=0, microsecond=0) <= now:
                            day = now + timedelta(days=7)
                    break

    if day is None and tod is None:
        return None
    if day is None:  # only a time: next occurrence
        h, mi = tod
        cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return cand if cand > now else cand + timedelta(days=1)
    h, mi = tod or (DEFAULT_HOUR, 0)
    return day.replace(hour=h, minute=mi, second=0, microsecond=0)


def describe(dt: datetime) -> str:
    return dt.strftime("%a %d %b, %I:%M%p").replace(" 0", " ").replace("AM", "am").replace("PM", "pm")
