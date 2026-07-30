"""Unit tests for app.voice.nlu.callback_time.parse_callback_time — the
deterministic parser that extracts a caller-requested callback time (e.g.
"call me tomorrow evening") from a NOT_NOW utterance."""
from __future__ import annotations

from datetime import datetime, timezone

from app.voice.nlu.callback_time import parse_callback_time

NOW = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)  # a Thursday


class TestNoTimeInfo:
    def test_bare_not_now_returns_none(self):
        assert parse_callback_time("not now", NOW) is None

    def test_bare_hebrew_not_now_returns_none(self):
        assert parse_callback_time("לא עכשיו", NOW) is None

    def test_empty_text_returns_none(self):
        assert parse_callback_time("", NOW) is None
        assert parse_callback_time(None, NOW) is None


class TestRelativeMinutesAndHours:
    def test_in_n_minutes_english(self):
        assert parse_callback_time("call me back in 30 minutes", NOW) == NOW.replace(minute=30)

    def test_in_n_minutes_hebrew(self):
        result = parse_callback_time("תתקשרו בעוד 45 דקות", NOW)
        assert result == NOW.replace(minute=45)

    def test_in_n_hours_english(self):
        result = parse_callback_time("call me in 2 hours", NOW)
        assert result == NOW.replace(hour=12)

    def test_in_n_hours_hebrew(self):
        result = parse_callback_time("בעוד 3 שעות תתקשרו", NOW)
        assert result == NOW.replace(hour=13)

    def test_in_an_hour_idiom_english(self):
        assert parse_callback_time("call me in an hour", NOW) == NOW.replace(hour=11)

    def test_in_an_hour_idiom_hebrew(self):
        assert parse_callback_time("בעוד שעה תתקשרו", NOW) == NOW.replace(hour=11)

    def test_half_an_hour_idiom(self):
        assert parse_callback_time("call me in half an hour", NOW) == NOW.replace(minute=30)
        assert parse_callback_time("בעוד חצי שעה", NOW) == NOW.replace(minute=30)


class TestDayAndTimeOfDay:
    def test_tomorrow_english_same_hour(self):
        result = parse_callback_time("call me tomorrow", NOW)
        assert result.date() == NOW.date().replace(day=NOW.day + 1)
        assert result.hour == NOW.hour

    def test_tomorrow_hebrew(self):
        result = parse_callback_time("מחר", NOW)
        assert result.day == NOW.day + 1

    def test_tomorrow_evening_english(self):
        result = parse_callback_time("call me back tomorrow evening", NOW)
        assert result.day == NOW.day + 1
        assert result.hour == 19

    def test_tomorrow_evening_hebrew(self):
        result = parse_callback_time("תתקשרו אליי מחר בערב", NOW)
        assert result.day == NOW.day + 1
        assert result.hour == 19

    def test_this_evening_before_evening_stays_today(self):
        # NOW is 10:00, "this evening" (19:00) hasn't happened yet today.
        result = parse_callback_time("call me this evening", NOW)
        assert result.day == NOW.day
        assert result.hour == 19

    def test_this_evening_after_evening_rolls_to_tomorrow(self):
        late_now = NOW.replace(hour=21)
        result = parse_callback_time("call me this evening", late_now)
        assert result.day == NOW.day + 1
        assert result.hour == 19

    def test_afternoon_does_not_match_noon_substring(self):
        # "אחר הצהריים" contains "צהריים" (noon) as a substring — the longer,
        # more specific phrase must win.
        result = parse_callback_time("תתקשרו אחר הצהריים", NOW)
        assert result.hour == 15


class TestSanityBounds:
    def test_far_future_unparseable_phrase_returns_none(self):
        assert parse_callback_time("call me next month sometime", NOW) is None

    def test_result_never_in_the_past(self):
        result = parse_callback_time("call me tomorrow morning", NOW.replace(hour=23))
        assert result is None or result > NOW.replace(hour=23)
