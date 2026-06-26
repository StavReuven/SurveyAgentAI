"""Named Entity Recognition — rule-based with optional Claude LLM fallback.

Extracts PERSON, PLACE, ORG, DATE, NUMBER entities from conversation turn text.
Stores results in the EntityMention table.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import ConversationTurn, EntityMention

# ── Rule patterns ─────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, str]] = [
    # Numbers / ratings
    (r"\b([1-9]|10)\s*(מתוך|out of|/)\s*10\b", "NUMBER"),
    (r"\b\d{1,2}[./]\d{1,2}([./]\d{2,4})?\b", "DATE"),
    (r"\b(ינואר|פברואר|מרץ|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר"
     r"|january|february|march|april|may|june|july|august|september|october|november|december)\b", "DATE"),
    (r"\b([א-ת]{2,}\s){1,3}(בע\"מ|בעמ|Ltd|LLC|Inc|Corp|ine)\b", "ORG"),
    (r"\b(tel aviv|תל אביב|ירושלים|jerusalem|חיפה|haifa|באר שבע|beer sheva|רמת גן|פתח תקווה|ראשון לציון)\b", "PLACE"),
    (r"\b(מר|גב'|ד\"ר|פרופ'?|mr\.?|mrs\.?|dr\.?|prof\.?)\s+[א-תa-zA-Z]{2,}", "PERSON"),
    (r"\b[A-Z][a-z]{2,}\s[A-Z][a-z]{2,}\b", "PERSON"),  # English names
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), etype) for pat, etype in _PATTERNS]


def extract_entities_from_text(text: str) -> list[dict]:
    """Return list of {entity_type, entity_value} dicts from text."""
    found: list[dict] = []
    seen: set[str] = set()
    for pattern, etype in _COMPILED:
        for m in pattern.finditer(text):
            val = m.group(0).strip()
            key = f"{etype}:{val.lower()}"
            if key not in seen:
                seen.add(key)
                found.append({"entity_type": etype, "entity_value": val, "confidence": 0.75, "method": "rule"})
    return found


def process_session_ner(session_id: str, campaign_id: int, db: Session) -> int:
    """Extract entities from all caller turns in a session. Returns count inserted."""
    turns = (
        db.query(ConversationTurn)
        .filter(
            ConversationTurn.session_id == session_id,
            ConversationTurn.speaker == "caller",
        )
        .all()
    )

    # Delete stale rule-based results for this session first
    db.query(EntityMention).filter(
        EntityMention.session_id == session_id,
        EntityMention.method == "rule",
    ).delete()

    inserted = 0
    for turn in turns:
        entities = extract_entities_from_text(turn.text or "")
        for e in entities:
            db.add(EntityMention(
                session_id=session_id,
                campaign_id=campaign_id,
                turn_id=turn.id,
                entity_type=e["entity_type"],
                entity_value=e["entity_value"],
                confidence=e["confidence"],
                method=e["method"],
                created_at=datetime.now(timezone.utc),
            ))
            inserted += 1
    db.flush()
    return inserted


async def process_session_ner_llm(session_id: str, campaign_id: int, db: Session) -> int:
    """LLM-based NER using Claude — called when ANTHROPIC_API_KEY is set."""
    import os
    try:
        import anthropic
    except ImportError:
        return process_session_ner(session_id, campaign_id, db)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return process_session_ner(session_id, campaign_id, db)

    turns = (
        db.query(ConversationTurn)
        .filter(
            ConversationTurn.session_id == session_id,
            ConversationTurn.speaker == "caller",
        )
        .all()
    )
    if not turns:
        return 0

    # Build transcript of caller turns
    transcript = "\n".join(f"[Turn {t.turn_index}] {t.text}" for t in turns)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""Extract named entities from this interview transcript. Return JSON array only.
Each item: {{"turn_index": int, "entity_type": "PERSON|PLACE|ORG|DATE|NUMBER|OTHER", "entity_value": "...", "confidence": 0.0-1.0}}

Transcript:
{transcript}

JSON array:"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = message.content[0].text.strip()
        # Extract JSON array from response
        start = text.find("[")
        end = text.rfind("]") + 1
        entities = json.loads(text[start:end]) if start >= 0 else []
    except Exception:
        return process_session_ner(session_id, campaign_id, db)

    # Map turn_index → turn.id
    turn_map = {t.turn_index: t for t in turns}

    db.query(EntityMention).filter(EntityMention.session_id == session_id).delete()
    inserted = 0
    for e in entities:
        turn = turn_map.get(e.get("turn_index"))
        db.add(EntityMention(
            session_id=session_id,
            campaign_id=campaign_id,
            turn_id=turn.id if turn else None,
            entity_type=e.get("entity_type", "OTHER"),
            entity_value=str(e.get("entity_value", ""))[:255],
            confidence=float(e.get("confidence", 0.8)),
            method="llm",
        ))
        inserted += 1
    db.flush()
    return inserted
