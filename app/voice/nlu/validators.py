"""Deterministic validators for structured free_text questions (age, city, ...),
keyed off QuestionContext.config["validate"].

Deliberately self-contained within app/voice/ — does not import from
app/intelligence/ (e.g. the city->district table in interviewee.py), so the
voice pipeline stays independent of the post-call intelligence layer. City
validation here is intentionally lenient (reject obvious non-answers only,
not a closed city list) since a caller from a small town not in any fixed
list is still a perfectly valid answer — the goal is catching "banana" or
"I don't know", not enumerating every real place name.
"""
from __future__ import annotations

import re

_AGE_RE = re.compile(r"\b(\d{1,3})\b")
MIN_AGE = 1
MAX_AGE = 120

_NON_ANSWER_RE = re.compile(
    r"^(לא יודע\w*|לא רוצה\w*|אין לי מושג|לא|אולי|"
    r"i\s*don'?t\s*know|not\s*sure|no\s*idea|n/?a|none|maybe|idk)$",
    re.IGNORECASE,
)


def validate_age(text: str) -> tuple[bool, int | None]:
    """Extract and range-check an age from free text. Returns (is_valid, age)."""
    m = _AGE_RE.search(text or "")
    if not m:
        return False, None
    age = int(m.group(1))
    if age < MIN_AGE or age > MAX_AGE:
        return False, None
    return True, age


def validate_city(text: str) -> tuple[bool, str | None]:
    """Screen out obvious non-answers to a city question. Returns (is_valid, city)."""
    cleaned = (text or "").strip()
    if len(cleaned) < 2:
        return False, None
    if _NON_ANSWER_RE.match(cleaned):
        return False, None
    if cleaned.replace(" ", "").isdigit():
        return False, None
    return True, cleaned


VALIDATORS = {
    "age": validate_age,
    "city": validate_city,
}


def clarification_for(validate_key: str, he: bool) -> str:
    """The re-ask line spoken back to the caller when validation fails."""
    if validate_key == "age":
        return "הגיל צריך להיות מספר. בן/בת כמה אתה/את?" if he else "Age needs to be a number. How old are you?"
    if validate_key == "city":
        return "לא הצלחתי לזהות שם עיר. באיזו עיר אתה גר?" if he else "I didn't catch a city name. Which city do you live in?"
    return "לא הבנתי את תשובתך. תוכל לנסח מחדש?" if he else "I didn't quite catch that — could you rephrase?"
