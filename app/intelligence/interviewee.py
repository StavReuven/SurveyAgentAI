"""Interviewee linking service.

Identifies a caller by phone number, creates or updates their Interviewee profile,
links all their Answers to the profile, and accumulates demographic data over time.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Answer, CallLog, EntityMention, Interviewee, Question

# Keyword patterns identifying a question as asking for a demographic field —
# same generic, keyword-overlap philosophy as cross_survey_facts.py rather
# than a rigid fixed schema, so any campaign's own phrasing of "what's your
# age" / "which city are you in" is picked up automatically.
_DEMOGRAPHIC_QUESTION_PATTERNS: dict[str, re.Pattern] = {
    "age": re.compile(r"age|old|גיל|בת כמה|בן כמה", re.I),
    "city": re.compile(r"city|location|live in|reside|עיר|מתגורר|גר ב", re.I),
}

_AGE_VALUE_RE = re.compile(r"\d{1,3}")

# City → district lookup, shared with app/analytics/router.py's bias
# computation (imported from there rather than duplicated). Not
# exhaustive — covers common cities per district; unrecognized cities are
# simply not classified. Districts per the user's own scheme: דרום, צפון,
# גוש דן, ירושלים.
DISTRICTS = ["דרום", "צפון", "גוש דן", "ירושלים"]

CITY_TO_DISTRICT: dict[str, str] = {
    # גוש דן
    "תל אביב": "גוש דן", "תל אביב-יפו": "גוש דן", "tel aviv": "גוש דן",
    "רמת גן": "גוש דן", "ramat gan": "גוש דן",
    "גבעתיים": "גוש דן", "givatayim": "גוש דן",
    "בני ברק": "גוש דן", "bnei brak": "גוש דן",
    "חולון": "גוש דן", "holon": "גוש דן",
    "בת ים": "גוש דן", "bat yam": "גוש דן",
    "פתח תקווה": "גוש דן", "petah tikva": "גוש דן", "petach tikva": "גוש דן",
    "הרצליה": "גוש דן", "herzliya": "גוש דן",
    "רמת השרון": "גוש דן", "ramat hasharon": "גוש דן",
    "כפר סבא": "גוש דן", "kfar saba": "גוש דן",
    "רעננה": "גוש דן", "raanana": "גוש דן", "ra'anana": "גוש דן",
    "הוד השרון": "גוש דן", "hod hasharon": "גוש דן",
    "אור יהודה": "גוש דן", "or yehuda": "גוש דן",
    "קריית אונו": "גוש דן", "kiryat ono": "גוש דן",
    "נתניה": "גוש דן", "netanya": "גוש דן",
    # ירושלים
    "ירושלים": "ירושלים", "jerusalem": "ירושלים",
    "מבשרת ציון": "ירושלים", "mevaseret zion": "ירושלים",
    "בית שמש": "ירושלים", "beit shemesh": "ירושלים",
    "מעלה אדומים": "ירושלים", "maale adumim": "ירושלים",
    # צפון
    "חיפה": "צפון", "haifa": "צפון",
    "נהריה": "צפון", "nahariya": "צפון",
    "עכו": "צפון", "acre": "צפון", "akko": "צפון",
    "טבריה": "צפון", "tiberias": "צפון",
    "צפת": "צפון", "safed": "צפון", "tzfat": "צפון",
    "כרמיאל": "צפון", "karmiel": "צפון",
    "קריית שמונה": "צפון", "kiryat shmona": "צפון",
    "עפולה": "צפון", "afula": "צפון",
    "נצרת": "צפון", "nazareth": "צפון",
    "חדרה": "צפון", "hadera": "צפון",
    "יקנעם": "צפון", "yokneam": "צפון",
    # דרום
    "באר שבע": "דרום", "beer sheva": "דרום", "beersheba": "דרום",
    "אשדוד": "דרום", "ashdod": "דרום",
    "אשקלון": "דרום", "ashkelon": "דרום",
    "אילת": "דרום", "eilat": "דרום",
    "דימונה": "דרום", "dimona": "דרום",
    "קריית גת": "דרום", "kiryat gat": "דרום",
    "ערד": "דרום", "arad": "דרום",
    "נתיבות": "דרום", "netivot": "דרום",
    "שדרות": "דרום", "sderot": "דרום",
    "אופקים": "דרום", "ofakim": "דרום",
}

# Sort longest-first so e.g. "תל אביב-יפו" matches before the shorter
# "תל אביב" substring would.
_CITY_NAMES_BY_LENGTH = sorted(CITY_TO_DISTRICT.keys(), key=len, reverse=True)


def _extract_city_from_text(text: str) -> str | None:
    """Find any known city name inside a free-text answer (e.g. "I live in
    Haifa." -> "haifa"), rather than storing the whole sentence — a raw
    sentence would never match the district lookup later."""
    lowered = (text or "").lower()
    for city in _CITY_NAMES_BY_LENGTH:
        if city in lowered:
            return city
    return None


# Every call now auto-asks these two questions ahead of the campaign's own
# (see app/main.py: _auto_demographic_questions) — so age/city extraction
# checks these fixed keys first. The fuzzy campaign-question search below
# is only a fallback, for calls that predate this (or a campaign that
# happens to also phrase its own age/city question some other way).
_AUTO_QUESTION_KEYS = {"age": "auto_age", "city": "auto_city"}


def _extract_demographics_from_session(
    session_id: str, campaign_id: int, db: Session
) -> dict:
    """Pull age/city straight into the Interviewee's demographics, from the
    auto-asked intake questions if present, else best-effort from any
    campaign question that looks like it's asking for the same thing."""
    found: dict[str, str] = {}

    for field, auto_key in _AUTO_QUESTION_KEYS.items():
        auto_answer = (
            db.query(Answer)
            .filter(Answer.session_id == session_id, Answer.question_key == auto_key)
            .first()
        )
        if auto_answer and auto_answer.raw_text:
            if field == "age":
                m = _AGE_VALUE_RE.search(auto_answer.raw_text)
                if m:
                    found["age"] = m.group(0)
            else:
                city = _extract_city_from_text(auto_answer.raw_text)
                if city:
                    found["city"] = city

    missing_fields = [f for f in _DEMOGRAPHIC_QUESTION_PATTERNS if f not in found]
    if not missing_fields:
        return found

    questions = db.query(Question).filter(Question.campaign_id == campaign_id).all()
    for field in missing_fields:
        pattern = _DEMOGRAPHIC_QUESTION_PATTERNS[field]
        q = next((q for q in questions if pattern.search(q.prompt or "")), None)
        if not q:
            continue
        answer = (
            db.query(Answer)
            .filter(Answer.session_id == session_id, Answer.question_key == q.key)
            .first()
        )
        if not answer or not answer.raw_text:
            continue
        if field == "age":
            m = _AGE_VALUE_RE.search(answer.raw_text)
            if m:
                found["age"] = m.group(0)
        elif field == "city":
            city = _extract_city_from_text(answer.raw_text)
            if city:
                found["city"] = city
    return found


def get_or_create_interviewee(phone_number: str, db: Session) -> Interviewee:
    """Return existing Interviewee or create a new one for this phone number."""
    interviewee = (
        db.query(Interviewee).filter(Interviewee.phone_number == phone_number).first()
    )
    if not interviewee:
        interviewee = Interviewee(
            phone_number=phone_number,
            demographics={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(interviewee)
        db.flush()
    return interviewee


def enrich_interviewee_from_session(
    session_id: str,
    participant_phone: str,
    db: Session,
    campaign_id: int | None = None,
) -> Interviewee:
    """
    Link all answers in this session to the Interviewee profile.
    Accumulate any PERSON entity detected as a name, plus any age/city-style
    answer if campaign_id is given.
    """
    interviewee = get_or_create_interviewee(participant_phone, db)

    # If this interviewee row was already loaded earlier in this same DB
    # session (e.g. build_interviewee_profiles looping over several sessions
    # for the same person), SQLAlchemy's identity map hands back that cached
    # Python object instead of re-querying — so a concurrently-committed
    # update from another session/thread (e.g. a live call's background
    # persistence) would be invisible here. Merging demographics onto that
    # stale copy and writing it back then silently clobbers the newer data.
    # Force a fresh read of the current row before merging.
    db.refresh(interviewee)

    # Link answer rows to this interviewee
    db.query(Answer).filter(Answer.session_id == session_id).update(
        {"interviewee_id": interviewee.id}, synchronize_session="fetch"
    )

    # Try to extract name from PERSON entities in this session
    name_entity = (
        db.query(EntityMention)
        .filter(
            EntityMention.session_id == session_id,
            EntityMention.entity_type == "PERSON",
        )
        .order_by(EntityMention.confidence.desc())
        .first()
    )
    if name_entity and not interviewee.full_name:
        interviewee.full_name = name_entity.entity_value

    # Count total campaigns this person has participated in
    session_campaign_ids = (
        db.query(CallLog.campaign_id)
        .filter(
            CallLog.session_id.in_(
                db.query(Answer.session_id).filter(Answer.interviewee_id == interviewee.id)
            )
        )
        .distinct()
        .all()
    )
    # Must be a genuinely new dict, not the same object as interviewee.demographics
    # (which "x or {}" returns when x is already non-empty) — SQLAlchemy's JSON
    # column change-tracking only fires on attribute reassignment to a different
    # object, so mutating and reassigning the SAME dict is silently never
    # persisted. This is why only the very first enrichment per person (when
    # demographics started as {}, a falsy value that forces a new object) ever
    # actually wrote to the DB — every later call for the same person appeared
    # to work (correct in-memory dict) but silently discarded the commit.
    demo = dict(interviewee.demographics or {})
    demo["campaigns_count"] = len(session_campaign_ids)
    demo["total_answers"] = (
        db.query(Answer).filter(Answer.interviewee_id == interviewee.id).count()
    )
    if campaign_id is not None:
        demo.update(_extract_demographics_from_session(session_id, campaign_id, db))
    interviewee.demographics = demo
    interviewee.updated_at = datetime.now(timezone.utc)
    db.flush()
    return interviewee


def build_interviewee_profiles(db: Session, organization_id: int) -> int:
    """
    Bulk-enrich all CallLog sessions that have a participant_id into
    Interviewee profiles (name, age/city, campaign counts). Scoped to a
    single organization — interviewee profiles are keyed by phone number
    only, so without this scope a person's data from one company's surveys
    could get pulled into another company's view of that same phone number.
    Returns count of sessions processed.
    """
    from ..models import Campaign, Participant

    logs_with_participant = (
        db.query(CallLog, Participant.phone_number)
        .join(Participant, Participant.id == CallLog.participant_id)
        .join(Campaign, Campaign.id == CallLog.campaign_id)
        .filter(CallLog.participant_id.isnot(None))
        .filter(Campaign.organization_id == organization_id)
        .all()
    )

    count = 0
    for log, phone in logs_with_participant:
        enrich_interviewee_from_session(log.session_id, phone, db, campaign_id=log.campaign_id)
        count += 1

    db.flush()
    return count
