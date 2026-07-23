"""Manual full-DB backfill for cross-survey matching — factual data extraction only.

Philosophy:
  Only match OBJECTIVE FACTS that a respondent stated in one survey and that
  happen to answer a specific question in another survey (within the same
  organization).

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

This script re-scans every existing answer in the DB — useful for backfilling
data that predates the automatic per-call matching now wired into
process_voice_turn (see app/main.py). For new calls, matching happens
automatically as soon as the call ends; you don't need to run this manually
anymore unless backfilling old data.
"""
import sys
sys.path.insert(0, '.')

from app.database import SessionLocal
from app.intelligence.cross_survey_facts import extract_facts, match_answer
from app.models import Answer


def run_matching():
    db = SessionLocal()
    try:
        free_texts = db.query(Answer).filter(
            Answer.answer_type == 'free_text',
            Answer.raw_text.isnot(None),
        ).all()

        print(f"Checking {len(free_texts)} free-text answers for factual data...\n")

        inserted = 0
        skipped_no_fact = 0

        for answer in free_texts:
            text = answer.raw_text or ''
            if len(text) < 10 or not extract_facts(text):
                skipped_no_fact += 1
                continue

            count = match_answer(db, answer)
            if count:
                print(
                    f"  [MATCH] campaign {answer.campaign_id} | "
                    f"{count} new match(es) from: '{text[:60]}...'"
                )
            inserted += count

        db.commit()
        print(f"\nDone:")
        print(f"  Factual matches created     : {inserted}")
        print(f"  Answers with no facts found : {skipped_no_fact}")

    finally:
        db.close()


if __name__ == '__main__':
    run_matching()
