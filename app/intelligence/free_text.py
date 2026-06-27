"""Free-text analysis — sentiment, intent, topics, key insights.

Rule-based fallback always available; Claude LLM used when ANTHROPIC_API_KEY is set.
Stores results in the FreeTextAnalysis table.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Answer, FreeTextAnalysis

# ── Keyword rules ─────────────────────────────────────────────────────────────

_POSITIVE_WORDS = {
    "מצוין", "מעולה", "נהדר", "מרוצה", "מומלץ", "ממליץ", "אהבתי", "מדהים", "שירות טוב",
    "excellent", "great", "happy", "satisfied", "recommend", "loved", "amazing", "wonderful",
}
_NEGATIVE_WORDS = {
    "גרוע", "רע", "נורא", "מאוכזב", "בעיה", "לא מרוצה", "לשפר", "חסר", "כישלון",
    "terrible", "bad", "awful", "disappointed", "problem", "issue", "poor", "failure", "improve",
}
_TOPIC_PATTERNS = {
    "שירות לקוחות": re.compile(r"שירות|נציג|עזרה|תמיכה|service|support|agent|help", re.I),
    "מחיר": re.compile(r"מחיר|יקר|זול|עלות|price|cost|expensive|cheap", re.I),
    "איכות מוצר": re.compile(r"מוצר|איכות|product|quality|item", re.I),
    "זמן המתנה": re.compile(r"המתנה|זמן|מהר|איטי|wait|time|slow|fast|quick", re.I),
    "חוויה כללית": re.compile(r"חוויה|תהליך|כללי|experience|overall|process|general", re.I),
}


def _rule_based_analysis(text: str) -> dict:
    words_lower = text.lower()
    word_set = set(re.findall(r"[א-ת\w]+", words_lower))

    pos = len(_POSITIVE_WORDS & word_set) + sum(1 for w in _POSITIVE_WORDS if w in words_lower)
    neg = len(_NEGATIVE_WORDS & word_set) + sum(1 for w in _NEGATIVE_WORDS if w in words_lower)

    if pos > neg:
        sentiment = "positive"
    elif neg > pos:
        sentiment = "negative"
    elif pos == neg == 0:
        sentiment = "neutral"
    else:
        sentiment = "mixed"

    topics = [topic for topic, pat in _TOPIC_PATTERNS.items() if pat.search(text)]
    if not topics:
        topics = ["כללי"]

    # Key insight: first sentence or up to 80 chars
    first_sentence = re.split(r"[.!?]", text.strip())[0][:80].strip()
    key_insights = [first_sentence] if first_sentence else []

    intent = "feedback" if sentiment in ("positive", "negative") else "information"

    return {
        "sentiment": sentiment,
        "intent": intent,
        "topics": topics,
        "key_insights": key_insights,
        "method": "keyword",
    }


async def _llm_analysis(text: str) -> dict | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""Analyze this survey free-text response and return JSON only:
{{
  "sentiment": "positive|negative|neutral|mixed",
  "intent": "one short phrase describing respondent's intent",
  "topics": ["list", "of", "topics"],
  "key_insights": ["list of key insights extracted from the text"]
}}

Text: "{text}"

JSON:"""
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) | {"method": "llm"}
    except Exception:
        return None


async def analyze_answer(answer: Answer, db: Session) -> FreeTextAnalysis:
    """Analyze one free-text answer and upsert a FreeTextAnalysis row."""
    if answer.answer_type != "free_text":
        raise ValueError("Only free_text answers can be analyzed")

    text = answer.raw_text or ""

    result = await _llm_analysis(text) or _rule_based_analysis(text)

    existing = db.query(FreeTextAnalysis).filter(FreeTextAnalysis.answer_id == answer.id).first()
    if existing:
        existing.sentiment = result["sentiment"]
        existing.intent = result.get("intent")
        existing.topics = result.get("topics", [])
        existing.key_insights = result.get("key_insights", [])
        existing.method = result.get("method", "keyword")
        existing.created_at = datetime.now(timezone.utc)
        row = existing
    else:
        row = FreeTextAnalysis(
            answer_id=answer.id,
            session_id=answer.session_id,
            campaign_id=answer.campaign_id,
            question_key=answer.question_key,
            sentiment=result["sentiment"],
            intent=result.get("intent"),
            topics=result.get("topics", []),
            key_insights=result.get("key_insights", []),
            method=result.get("method", "keyword"),
        )
        db.add(row)
    db.flush()
    return row


async def analyze_campaign_free_text(campaign_id: int, db: Session) -> int:
    """Analyze all free-text answers for a campaign. Returns count processed."""
    answers = (
        db.query(Answer)
        .filter(Answer.campaign_id == campaign_id, Answer.answer_type == "free_text")
        .all()
    )
    count = 0
    for answer in answers:
        try:
            await analyze_answer(answer, db)
            count += 1
        except Exception:
            pass
    db.flush()
    return count
