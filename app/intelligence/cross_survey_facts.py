"""Regex/keyword-based cross-survey fact matching — the functional half of
cross-survey matching that works without an ANTHROPIC_API_KEY (unlike the
LLM-based app/intelligence/cross_survey.py, which is a no-op without one).

Shared by run_cross_survey.py (manual full-DB backfill) and process_voice_turn
in app/main.py (automatic, per-answer, right after a call ends).
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import Answer, Campaign, CrossSurveyMatch, Question

# Each entry: (topic_key, extraction_regex, question_keywords)
#
# extraction_regex must have group(1) = the numeric value.
# question_keywords: if ANY of these appear in the question prompt → it's a match.
FACTUAL_PATTERNS = [
    # Study hours
    (
        "study_hours",
        re.compile(
            r'(\d+(?:\.\d+)?)\s*'
            r'(?:שעות?\s*(?:לימוד|לימודים|ללמוד|לפני\s*המבחן)|'
            r'hours?\s*(?:of\s*)?(?:study|studying|learning|before\s*exam))',
            re.I
        ),
        ["כמה שעות למדת", "כמה שעות לימוד", "how many hours.*stud",
         "hours of study", "hours did you study"],
    ),
    # Sleep hours
    (
        "sleep_hours",
        re.compile(
            r'(\d+(?:\.\d+)?)\s*'
            r'(?:שעות?\s*(?:שינה|ישנתי|לישון)|'
            r'hours?\s*(?:of\s*)?(?:sleep|sleeping|slept))',
            re.I
        ),
        ["כמה שעות ישנת", "כמה שעות שינה", "how many hours.*sleep",
         "hours did you sleep", "hours of sleep"],
    ),
    # Exercise / sport frequency (times per week)
    (
        "exercise_frequency",
        re.compile(
            r'(\d+)\s*'
            r'(?:פעמים?\s*(?:בשבוע|לשבוע)|'
            r'times?\s*(?:a|per)\s*week)\s*'
            r'(?:ספורט|אימון|exercise|workout|gym)?',
            re.I
        ),
        ["כמה פעמים בשבוע", "how many times.*week", "exercise.*week",
         "workout.*week", "ספורט בשבוע", "אימון בשבוע"],
    ),
    # Age
    (
        "age",
        re.compile(
            r'(?:בן|בת|גילי|גיל)\s*(\d{1,3})|'
            r'(?:i am|i\'m|age)\s+(\d{1,3})\s*(?:years?)?',
            re.I
        ),
        ["כמה שנים", "מה גילך", "בן כמה", "how old", "what.*age", "your age"],
    ),
    # Money / spending
    (
        "money_spent",
        re.compile(
            r'(\d+(?:[,\.]\d+)?)\s*'
            r'(?:שקל|ש"ח|nis|ils|dollar|\$|euro|€)',
            re.I
        ),
        ["כמה שילמת", "כמה עלה", "how much.*cost", "how much.*pay",
         "how much.*spend", "price", "מחיר", "עלות"],
    ),
    # Commute / travel time (minutes)
    (
        "commute_minutes",
        re.compile(
            r'(\d+)\s*'
            r'(?:דקות?\s*(?:נסיעה|הליכה|דרך)|'
            r'minutes?\s*(?:commute|travel|drive|walk|ride))',
            re.I
        ),
        ["כמה זמן נסיעה", "כמה דקות", "how long.*commute", "travel time",
         "how many minutes"],
    ),
    # Number of meals per day
    (
        "meals_per_day",
        re.compile(
            r'(\d)\s*(?:ארוחות?\s*(?:ביום|לדין)|meals?\s*(?:a|per)\s*day)',
            re.I
        ),
        ["כמה ארוחות", "how many meals", "meals per day", "ארוחות ביום"],
    ),
    # Screen / phone time (hours per day)
    (
        "screen_hours",
        re.compile(
            r'(\d+(?:\.\d+)?)\s*'
            r'(?:שעות?\s*(?:מסך|טלפון|פלאפון|מחשב)|'
            r'hours?\s*(?:of\s*)?(?:screen|phone|computer|device))',
            re.I
        ),
        ["כמה שעות מסך", "כמה שעות טלפון", "screen time", "phone time",
         "hours.*screen", "hours.*phone"],
    ),
    # Water / liquids per day (glasses / liters)
    (
        "water_intake",
        re.compile(
            r'(\d+(?:\.\d+)?)\s*'
            r'(?:כוסות?\s*מים|ליטר\s*מים|glasses?\s*of\s*water|liters?\s*of\s*water)',
            re.I
        ),
        ["כמה כוסות מים", "כמה מים", "how many glasses", "water intake",
         "liters of water"],
    ),
]


def extract_facts(text: str) -> list[tuple[str, str]]:
    """Return list of (topic_key, value_str) from free text."""
    results = []
    for topic_key, pattern, _ in FACTUAL_PATTERNS:
        m = pattern.search(text)
        if m:
            val = next((g for g in m.groups() if g is not None), None)
            if val:
                results.append((topic_key, val.replace(',', '')))
    return results


def question_matches_topic(question: Question, topic_key: str) -> bool:
    """True if the question is asking about the given factual topic."""
    prompt = (question.prompt or '').lower()
    _, _, keywords = next(
        (p for p in FACTUAL_PATTERNS if p[0] == topic_key), (None, None, [])
    )
    return any(re.search(kw, prompt, re.I) for kw in (keywords or []))


def match_answer(db: Session, answer: Answer) -> int:
    """Find and record cross-survey matches for a single answer.

    Scoped to campaigns in the same organization as the answer's own
    campaign — matching must never cross organization boundaries (an
    answer from one company's survey shouldn't leak into another
    company's campaign data).

    Returns the number of new CrossSurveyMatch rows created.
    """
    text = answer.raw_text or ''
    if len(text) < 10:
        return 0

    facts = extract_facts(text)
    if not facts:
        return 0

    source_campaign = db.get(Campaign, answer.campaign_id)
    if not source_campaign:
        return 0

    other_questions = (
        db.query(Question)
        .join(Campaign, Campaign.id == Question.campaign_id)
        .filter(
            Question.campaign_id != answer.campaign_id,
            Campaign.organization_id == source_campaign.organization_id,
        )
        .all()
    )
    if not other_questions:
        return 0

    src_q = db.query(Question).filter(
        Question.campaign_id == answer.campaign_id,
        Question.key == answer.question_key,
    ).first()

    inserted = 0
    for q in other_questions:
        if q.question_type not in ('rating', 'free_text'):
            continue

        for topic_key, extracted_value in facts:
            if not question_matches_topic(q, topic_key):
                continue

            exists = db.query(CrossSurveyMatch).filter(
                CrossSurveyMatch.source_answer_id == answer.id,
                CrossSurveyMatch.target_campaign_id == q.campaign_id,
                CrossSurveyMatch.target_question_key == q.key,
            ).first()
            if exists:
                continue

            db.add(CrossSurveyMatch(
                source_answer_id=answer.id,
                source_campaign_id=answer.campaign_id,
                source_question_key=answer.question_key,
                source_question_prompt=(src_q.prompt[:512] if src_q else None),
                source_answer_text=text[:1000],
                target_campaign_id=q.campaign_id,
                target_question_key=q.key,
                target_question_prompt=(q.prompt[:512] if q.prompt else None),
                matched_topics=topic_key,
                match_confidence=0.85,
            ))

            already = db.query(Answer).filter(
                Answer.session_id == answer.session_id,
                Answer.campaign_id == q.campaign_id,
                Answer.question_key == q.key,
            ).first()
            if not already:
                db.add(Answer(
                    session_id=answer.session_id,
                    campaign_id=q.campaign_id,
                    question_id=q.id,
                    question_key=q.key,
                    raw_text=extracted_value,
                    normalized_value=extracted_value,
                    answer_type=q.question_type,
                ))

            inserted += 1

    return inserted
