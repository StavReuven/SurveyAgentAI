"""Fact-checking service for survey answers.

For rating-type answers: validates that the value is within the allowed range.
For MCQ answers: validates the value is one of the configured options.
For free-text: uses Claude to assess claim credibility (when API key is set).
Stores results in AnswerFactCheck.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Answer, AnswerFactCheck, Question


def _check_rating(answer: Answer, question: Question) -> dict:
    config = question.config or {}
    min_val = config.get("min", 1)
    max_val = config.get("max", 10)
    try:
        val = float(re.search(r"\d+(\.\d+)?", answer.raw_text or "").group())
        if min_val <= val <= max_val:
            return {
                "claim": f"ציון {val} בטווח {min_val}-{max_val}",
                "verdict": "true",
                "confidence": 1.0,
                "explanation": f"הערך {val} תקין בטווח שהוגדר ({min_val}-{max_val})",
            }
        return {
            "claim": f"ציון {val} בטווח {min_val}-{max_val}",
            "verdict": "false",
            "confidence": 1.0,
            "explanation": f"הערך {val} מחוץ לטווח המותר ({min_val}-{max_val})",
        }
    except (AttributeError, ValueError):
        return {
            "claim": answer.raw_text or "",
            "verdict": "uncertain",
            "confidence": 0.5,
            "explanation": "לא ניתן לחלץ ערך מספרי מהתשובה",
        }


def _check_mcq(answer: Answer, question: Question) -> dict:
    config = question.config or {}
    options = [str(o).lower() for o in config.get("options", [])]
    value = (answer.normalized_value or "").lower()
    if not options:
        return {
            "claim": value,
            "verdict": "not_checkable",
            "confidence": 0.0,
            "explanation": "אין אפשרויות מוגדרות לשאלה זו",
        }
    if value in options or any(value in opt for opt in options):
        return {
            "claim": value,
            "verdict": "true",
            "confidence": 0.95,
            "explanation": f"התשובה '{value}' היא אפשרות חוקית",
        }
    return {
        "claim": value,
        "verdict": "false",
        "confidence": 0.9,
        "explanation": f"התשובה '{value}' אינה אחת מהאפשרויות: {', '.join(options)}",
    }


async def _check_free_text_llm(answer: Answer, question: Question) -> dict | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are a fact-checker for a survey answer. Evaluate whether the following answer is factually credible.
Survey question: "{question.prompt}"
Answer: "{answer.raw_text}"

Return JSON only:
{{"claim": "brief description of the claim", "verdict": "true|false|uncertain|not_checkable", "confidence": 0.0-1.0, "explanation": "brief Hebrew explanation"}}

JSON:"""
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return None


async def fact_check_answer(answer: Answer, db: Session) -> AnswerFactCheck:
    """Run fact-check on one answer. Returns upserted AnswerFactCheck row."""
    question = db.query(Question).filter(Question.id == answer.question_id).first()

    if question and answer.answer_type == "rating":
        result = _check_rating(answer, question)
        result["method"] = "rule"
    elif question and answer.answer_type == "mcq":
        result = _check_mcq(answer, question)
        result["method"] = "rule"
    elif answer.answer_type == "free_text":
        llm_result = await _check_free_text_llm(answer, question) if question else None
        if llm_result:
            result = llm_result | {"method": "llm"}
        else:
            result = {
                "claim": (answer.raw_text or "")[:120],
                "verdict": "not_checkable",
                "confidence": 0.0,
                "explanation": "בדיקת עובדות לטקסט חופשי דורשת מפתח API",
                "method": "rule",
            }
    else:
        result = {
            "claim": answer.raw_text or "",
            "verdict": "not_checkable",
            "confidence": 0.0,
            "explanation": "סוג תשובה לא ידוע",
            "method": "rule",
        }

    existing = db.query(AnswerFactCheck).filter(AnswerFactCheck.answer_id == answer.id).first()
    if existing:
        existing.claim = result["claim"][:500]
        existing.verdict = result["verdict"]
        existing.confidence = result["confidence"]
        existing.explanation = result.get("explanation")
        existing.method = result["method"]
        existing.checked_at = datetime.now(timezone.utc)
        row = existing
    else:
        row = AnswerFactCheck(
            answer_id=answer.id,
            campaign_id=answer.campaign_id,
            claim=result["claim"][:500],
            verdict=result["verdict"],
            confidence=result["confidence"],
            explanation=result.get("explanation"),
            method=result["method"],
        )
        db.add(row)
    db.flush()
    return row


async def fact_check_campaign(campaign_id: int, db: Session) -> int:
    """Fact-check all answers in a campaign. Returns count processed."""
    answers = db.query(Answer).filter(Answer.campaign_id == campaign_id).all()
    count = 0
    for answer in answers:
        try:
            await fact_check_answer(answer, db)
            count += 1
        except Exception:
            pass
    db.flush()
    return count
