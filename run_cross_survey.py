"""Semantic cross-survey matching — factual data extraction only.

Philosophy:
  Only match OBJECTIVE FACTS that a respondent stated in one survey and that
  happen to answer a specific question in another survey.

  Good match example:
    Survey A free-text: "I studied 6 hours and slept 7 hours last night."
    Survey B question:  "How many hours did you sleep last night?"
    → Extract "7" and attach it to Survey B's sleep question.

  Bad match (excluded):
    Survey A rating:    "9/10 for the service"
    Survey B question:  "How satisfied are you with the service? (1-10)"
    → SKIP — a satisfaction score from one context ≠ the same context.

  Key rule: only numeric values that carry a UNIT/CATEGORY label
  (hours, times per week, money, distance, age…) can cross surveys.
  Bare ratings without a factual anchor are NOT transferred.
"""
import re
import sys
sys.path.insert(0, '.')

from app.database import SessionLocal
from app.models import Answer, Question, CrossSurveyMatch

# ── Factual extractors ────────────────────────────────────────────────────────
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


def _extract_facts(text: str) -> list[tuple[str, str]]:
    """Return list of (topic_key, value_str) from free text."""
    results = []
    for topic_key, pattern, _ in FACTUAL_PATTERNS:
        m = pattern.search(text)
        if m:
            # Age pattern has two groups
            val = next((g for g in m.groups() if g is not None), None)
            if val:
                results.append((topic_key, val.replace(',', '')))
    return results


def _question_matches_topic(question: Question, topic_key: str) -> bool:
    """True if the question is asking about the given factual topic."""
    prompt = (question.prompt or '').lower()
    _, _, keywords = next(
        (p for p in FACTUAL_PATTERNS if p[0] == topic_key), (None, None, [])
    )
    return any(re.search(kw, prompt, re.I) for kw in (keywords or []))


# ── Main ──────────────────────────────────────────────────────────────────────

def run_matching():
    db = SessionLocal()
    try:
        free_texts = db.query(Answer).filter(
            Answer.answer_type == 'free_text',
            Answer.raw_text.isnot(None),
        ).all()

        print(f"Checking {len(free_texts)} free-text answers for factual data...\n")

        all_questions = db.query(Question).all()
        questions_by_campaign: dict[int, list[Question]] = {}
        for q in all_questions:
            questions_by_campaign.setdefault(q.campaign_id, []).append(q)

        inserted = 0
        skipped_no_fact = 0
        skipped_no_question_match = 0

        for answer in free_texts:
            text = answer.raw_text or ''
            if len(text) < 10:
                continue

            facts = _extract_facts(text)
            if not facts:
                skipped_no_fact += 1
                continue

            for campaign_id, questions in questions_by_campaign.items():
                if campaign_id == answer.campaign_id:
                    continue

                for q in questions:
                    if q.question_type not in ('rating', 'free_text'):
                        continue

                    for topic_key, extracted_value in facts:
                        if not _question_matches_topic(q, topic_key):
                            skipped_no_question_match += 1
                            continue

                        # Skip duplicates
                        exists = db.query(CrossSurveyMatch).filter(
                            CrossSurveyMatch.source_answer_id == answer.id,
                            CrossSurveyMatch.target_campaign_id == campaign_id,
                            CrossSurveyMatch.target_question_key == q.key,
                        ).first()
                        if exists:
                            continue

                        src_q = db.query(Question).filter(
                            Question.campaign_id == answer.campaign_id,
                            Question.key == answer.question_key,
                        ).first()

                        db.add(CrossSurveyMatch(
                            source_answer_id=answer.id,
                            source_campaign_id=answer.campaign_id,
                            source_question_key=answer.question_key,
                            source_question_prompt=(src_q.prompt[:512] if src_q else None),
                            source_answer_text=text[:1000],
                            target_campaign_id=campaign_id,
                            target_question_key=q.key,
                            target_question_prompt=(q.prompt[:512] if q.prompt else None),
                            matched_topics=topic_key,
                            match_confidence=0.85,
                        ))

                        already = db.query(Answer).filter(
                            Answer.session_id == answer.session_id,
                            Answer.campaign_id == campaign_id,
                            Answer.question_key == q.key,
                        ).first()
                        if not already:
                            db.add(Answer(
                                session_id=answer.session_id,
                                campaign_id=campaign_id,
                                question_id=q.id,
                                question_key=q.key,
                                raw_text=extracted_value,
                                normalized_value=extracted_value,
                                answer_type=q.question_type,
                            ))

                        print(
                            f"  [MATCH] {topic_key} | "
                            f"camp {answer.campaign_id}->{campaign_id} | "
                            f"extracted '{extracted_value}' from: "
                            f"'{text[:60]}...'"
                        )
                        inserted += 1

        db.commit()
        print(f"\nDone:")
        print(f"  Factual matches created        : {inserted}")
        print(f"  Answers with no facts found    : {skipped_no_fact}")
        print(f"  Facts with no matching question: {skipped_no_question_match}")

    finally:
        db.close()


if __name__ == '__main__':
    run_matching()
