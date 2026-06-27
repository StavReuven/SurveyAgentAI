"""Cross-survey semantic answer matching using Claude.

For each answer in a campaign, checks whether its TEXT actually answers
a question from a DIFFERENT campaign - semantically, not just by key name.

Example:
  Survey A Q: "How was your day at school?"
  Answer:     "Great, I studied for 7 hours"
  Survey B Q: "How many hours did you study today?"
  → Match! Extract "7 hours" and create an Answer row in Survey B.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Answer, Campaign, CrossSurveyMatch, Question


async def _llm_check_match(
    answer_text: str,
    source_question: str,
    target_question: str,
    target_type: str,
) -> dict | None:
    """Ask Claude if answer_text answers target_question. Returns extracted value or None."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    type_hint = {
        "rating": "a numeric rating (e.g. 7, or '7 out of 10')",
        "mcq": "one of the multiple-choice options",
        "free_text": "a relevant text excerpt",
    }.get(target_type, "a relevant value")

    prompt = f"""You are analyzing survey answers for cross-survey matching.

Original question (Survey A): "{source_question}"
Answer given: "{answer_text}"

Target question (Survey B): "{target_question}"
Expected answer type: {type_hint}

Does the answer to Survey A's question DIRECTLY contain information that answers Survey B's question?
Only say YES if the answer genuinely and specifically answers the target question - not just because both are about the same broad topic.

Respond with JSON only:
{{"match": true/false, "confidence": 0.0-1.0, "extracted_value": "the specific value that answers the target question, or null", "explanation": "one sentence why"}}

JSON:"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return None


async def find_cross_survey_matches(
    campaign_id: int,
    db: Session,
    min_confidence: float = 0.7,
) -> int:
    """Semantically match answers from campaign_id to questions in other campaigns.

    For each answer, asks Claude whether the answer text actually answers
    questions from other campaigns. If yes, stores a CrossSurveyMatch AND
    creates a real Answer row in the target campaign's session.
    """
    answers = db.query(Answer).filter(Answer.campaign_id == campaign_id).all()
    if not answers:
        return 0

    # Build: question_id → Question for all other campaigns
    other_questions = (
        db.query(Question)
        .filter(Question.campaign_id != campaign_id)
        .all()
    )
    if not other_questions:
        return 0

    # Get source question prompts
    source_questions = {
        q.key: q for q in db.query(Question).filter(Question.campaign_id == campaign_id).all()
    }

    inserted = 0
    for answer in answers:
        if not answer.raw_text or len(answer.raw_text.strip()) < 3:
            continue

        source_q = source_questions.get(answer.question_key)
        source_prompt = source_q.prompt if source_q else answer.question_key

        for target_q in other_questions:
            # Skip same question type mismatch for rating (numbers must match numbers)
            if answer.answer_type == "rating" and target_q.question_type != "rating":
                continue

            # Skip if already matched
            exists = (
                db.query(CrossSurveyMatch)
                .filter(
                    CrossSurveyMatch.source_answer_id == answer.id,
                    CrossSurveyMatch.target_campaign_id == target_q.campaign_id,
                    CrossSurveyMatch.target_question_key == target_q.key,
                )
                .first()
            )
            if exists:
                continue

            result = await _llm_check_match(
                answer_text=answer.raw_text,
                source_question=source_prompt,
                target_question=target_q.prompt,
                target_type=target_q.question_type,
            )

            if not result or not result.get("match"):
                continue

            confidence = result.get("confidence", 0.0)
            if confidence < min_confidence:
                continue

            extracted = result.get("extracted_value") or answer.raw_text

            # Save the match record
            db.add(CrossSurveyMatch(
                source_answer_id=answer.id,
                source_campaign_id=campaign_id,
                target_campaign_id=target_q.campaign_id,
                target_question_key=target_q.key,
                match_confidence=round(confidence, 3),
            ))

            # Create a real Answer in the target campaign so it shows in analytics
            already_answered = (
                db.query(Answer)
                .filter(
                    Answer.session_id == answer.session_id,
                    Answer.campaign_id == target_q.campaign_id,
                    Answer.question_key == target_q.key,
                )
                .first()
            )
            if not already_answered:
                db.add(Answer(
                    session_id=answer.session_id,
                    campaign_id=target_q.campaign_id,
                    question_id=target_q.id,
                    question_key=target_q.key,
                    raw_text=extracted,
                    normalized_value=extracted,
                    answer_type=target_q.question_type,
                    created_at=datetime.now(timezone.utc),
                ))

            inserted += 1
            print(f"  [cross] campaign {campaign_id} -> {target_q.campaign_id} | "
                  f"'{answer.raw_text[:40]}' answers '{target_q.prompt[:40]}' "
                  f"(conf={confidence:.2f})")

    db.flush()
    return inserted


async def run_all_campaigns_matching(db: Session) -> dict[int, int]:
    """Run semantic cross-survey matching for every campaign."""
    campaigns = db.query(Campaign).all()
    results = {}
    for c in campaigns:
        count = await find_cross_survey_matches(c.id, db)
        results[c.id] = count
    return results
