"""Best-effort deterministic parser for a caller-requested callback time
(e.g. "call me back tomorrow evening" / "תתקשרו אליי מחר בערב"), used only
to refine NOT_NOW retry scheduling — never validates or blocks anything, so
a phrase it can't parse just returns None and the caller falls back to the
campaign's generic retry_delay_minutes policy, exactly as before this module
existed. Deliberately self-contained within app/voice/, no app/intelligence/
import, matching the layering already established by validators.py.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# Representative hour for a named part of the day. Approximate on purpose —
# this only has to land the retry inside the right multi-hour window, not
# hit an exact minute.
_TIME_OF_DAY = [
    ("אחר הצהריים", 15), ("afternoon", 15),
    ("בבוקר", 9), ("morning", 9),
    ("בצהריים", 12), ("noon", 12),
    ("בערב", 19), ("evening", 19),
    ("בלילה", 21), ("night", 21),
]
# Longest phrase first so "אחר הצהריים" wins over the "בצהריים"/"noon"-style
# substring it contains.
_TIME_OF_DAY.sort(key=lambda pair: len(pair[0]), reverse=True)

_MINUTES_RE = re.compile(
    r"in\s+(\d{1,3})\s*min(?:ute)?s?|בעוד\s+(\d{1,3})\s*דקות?", re.IGNORECASE
)
_HOURS_RE = re.compile(
    r"in\s+(\d{1,2})\s*hours?|בעוד\s+(\d{1,2})\s*שעות?", re.IGNORECASE
)
# "in an hour" / "בעוד שעה" — the common idiom with no digit at all.
_HALF_HOUR_RE = re.compile(r"in\s+half\s+an?\s+hour|בעוד\s+חצי\s+שעה", re.IGNORECASE)
_ONE_HOUR_RE = re.compile(r"in\s+an?\s+hour\b|בעוד\s+שעה\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b|מחר", re.IGNORECASE)
_TODAY_RE = re.compile(r"\btoday\b|היום", re.IGNORECASE)

# Sane bounds — a parsed time outside this window is more likely a
# misfire than a real request, so it's discarded rather than acted on.
_MAX_LOOKAHEAD = timedelta(days=14)


def parse_callback_time(text: str, now: datetime) -> datetime | None:
    """Extract a caller-requested callback time from free text, if any.

    Returns None (meaning: no specific time was recognised — the caller
    should fall back to the generic retry policy) far more often than a
    real datetime; this is intentional, since a wrong guess at *when*
    someone wants to be called back is worse than just using the default.
    """
    t = (text or "").lower()
    if not t.strip():
        return None

    if _HALF_HOUR_RE.search(t):
        return now + timedelta(minutes=30)

    m = _MINUTES_RE.search(t)
    if m:
        minutes = int(m.group(1) or m.group(2))
        if minutes > 0:
            return now + timedelta(minutes=minutes)

    if _ONE_HOUR_RE.search(t):
        return now + timedelta(hours=1)

    m = _HOURS_RE.search(t)
    if m:
        hours = int(m.group(1) or m.group(2))
        if hours > 0:
            return now + timedelta(hours=hours)

    day_offset: int | None = None
    if _TOMORROW_RE.search(t):
        day_offset = 1
    elif _TODAY_RE.search(t):
        day_offset = 0

    hour: int | None = None
    for phrase, h in _TIME_OF_DAY:
        if phrase in t:
            hour = h
            break

    if day_offset is None and hour is None:
        return None

    target = (now + timedelta(days=day_offset or 0)).replace(
        minute=0, second=0, microsecond=0
    )
    if hour is not None:
        target = target.replace(hour=hour)
    if day_offset is None and target <= now:
        # A bare time-of-day with no explicit day ("this evening") that has
        # already passed today clearly means tomorrow instead.
        target += timedelta(days=1)

    if target <= now or target - now > _MAX_LOOKAHEAD:
        return None
    return target
