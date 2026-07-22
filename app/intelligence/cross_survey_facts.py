"""Generic, rule-based cross-survey fact matching — no LLM required (unlike
app/intelligence/cross_survey.py, which is a no-op without ANTHROPIC_API_KEY).

Instead of a fixed list of known topics (sleep hours, age, etc.), this finds
ANY number in a free-text answer, looks at the words around it, and checks
whether those words overlap with a target question's own wording (once
filler words like "how many"/"כמה" are stripped out). This means a brand
new question created in the UI works automatically — there's no per-topic
regex to add by hand.

Shared by run_cross_survey.py (manual full-DB backfill) and process_voice_turn
in app/main.py (automatic, per-answer, right after a call ends).
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import Answer, Campaign, CrossSurveyMatch, Question

# Words that carry no topical meaning on their own — stripped from both the
# answer's context window and the target question's prompt before comparing,
# so that e.g. "how many hours did you sleep" and "hours of sleep" reduce to
# the same core terms ({hours, sleep}) instead of matching on "how"/"did"/"of".
_STOPWORDS = {
    "how", "many", "much", "what", "when", "is", "are", "was", "were", "did",
    "do", "does", "you", "your", "the", "a", "an", "of", "in", "on", "at",
    "to", "for", "and", "or", "i", "it", "this", "that", "last", "night",
    "yesterday", "today", "per", "a", "week", "day", "please", "tell", "me",
    "about",
    "כמה", "מה", "האם", "שלך", "שלכם", "אתמול", "היום", "אמש", "בשבוע",
    "ביום", "אני", "את", "אתה", "זה", "הוא", "היא", "על", "עם", "של",
}

_WORD_RE = re.compile(r"[a-zA-Z֐-׿]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# Spelled-out numbers, so "third year" or "בשנה השלישית" extract just as well
# as a literal digit would. Covers cardinals and ordinals 1-12 in English and
# Hebrew (masculine + feminine forms) — enough for the common cases (ages,
# academic year, count of times) without trying to be a full NLP number parser.
_NUMBER_WORDS = {
    # English cardinals
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
    # English ordinals
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12",
    # Hebrew cardinals (m/f forms)
    "אחד": "1", "אחת": "1", "שניים": "2", "שתיים": "2", "שלושה": "3",
    "שלוש": "3", "ארבעה": "4", "ארבע": "4", "חמישה": "5", "חמש": "5",
    "שישה": "6", "שש": "6", "שבעה": "7", "שבע": "7", "שמונה": "8",
    "תשעה": "9", "תשע": "9", "עשרה": "10", "עשר": "10",
    # Hebrew ordinals (m/f forms)
    "ראשון": "1", "ראשונה": "1", "שני": "2", "שנייה": "2", "שלישי": "3",
    "שלישית": "3", "רביעי": "4", "רביעית": "4", "חמישי": "5", "חמישית": "5",
    "שישי": "6", "שישית": "6", "שביעי": "7", "שביעית": "7", "שמיני": "8",
    "שמינית": "8", "תשיעי": "9", "תשיעית": "9", "עשירי": "10", "עשירית": "10",
}

# Minimum number of shared, meaningful words between an answer's context and
# a question's own wording before we trust it's actually the same topic —
# without this, a lone shared word like "hours" would match almost anything.
_MIN_OVERLAP = 2


def _content_words(text: str) -> set[str]:
    return {
        w for w in _WORD_RE.findall((text or "").lower())
        if w not in _STOPWORDS and w not in _NUMBER_WORDS and len(w) > 1
    }


def extract_facts(text: str, window: int = 4) -> list[tuple[str, set[str]]]:
    """Find every number in the text — as a digit or a spelled-out word
    ("third", "שלישית") — paired with the meaningful words around it (its
    "topic context"). Returns [(number_str, context_words)]."""
    words = (text or "").split()
    results = []
    for i, tok in enumerate(words):
        m = _NUMBER_RE.search(tok)
        if m:
            value = m.group(0)
        else:
            cleaned = _WORD_RE.findall(tok.lower())
            word_key = cleaned[0] if cleaned else None
            if not word_key or word_key not in _NUMBER_WORDS:
                continue
            value = _NUMBER_WORDS[word_key]
        start, end = max(0, i - window), min(len(words), i + window + 1)
        context = _content_words(" ".join(words[start:end]))
        if context:
            results.append((value, context))
    return results


def question_matches_context(question: Question, context_words: set[str]) -> set[str]:
    """Return the overlapping words if this question is plausibly asking
    about the same thing as `context_words`, else an empty set."""
    q_terms = _content_words(question.prompt or "")
    if not q_terms:
        return set()
    overlap = context_words & q_terms
    if len(overlap) >= min(_MIN_OVERLAP, len(q_terms)):
        return overlap
    return set()


def match_answer(db: Session, answer: Answer) -> int:
    """Find and record cross-survey matches for a single answer.

    Scoped to campaigns in the same organization as the answer's own
    campaign — matching must never cross organization boundaries (an
    answer from one company's survey shouldn't leak into another
    company's campaign data).

    Returns the number of new CrossSurveyMatch rows created.
    """
    text = answer.raw_text or ""
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
        if q.question_type not in ("rating", "free_text"):
            continue

        for extracted_value, context_words in facts:
            overlap = question_matches_context(q, context_words)
            if not overlap:
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
                matched_topics="+".join(sorted(overlap))[:255],
                match_confidence=0.7 + 0.1 * min(len(overlap) - _MIN_OVERLAP, 2),
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
