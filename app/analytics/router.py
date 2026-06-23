"""Analytics API — per-campaign answer breakdowns for charts and KPIs."""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Answer, CallLog, Campaign, Question

router = APIRouter(prefix="/api/campaigns/{campaign_id}/analytics", tags=["analytics"])


def _get_campaign_or_404(campaign_id: int, db: Session) -> Campaign:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.get("/summary")
def analytics_summary(campaign_id: int, db: Session = Depends(get_db)):
    """
    High-level KPIs for a campaign:
    - total_sessions, completed_sessions, response_rate
    - per-question answer distribution (counts + percentages)
    """
    _get_campaign_or_404(campaign_id, db)

    total = db.query(func.count(CallLog.id)).filter(CallLog.campaign_id == campaign_id).scalar() or 0
    completed = (
        db.query(func.count(CallLog.id))
        .filter(CallLog.campaign_id == campaign_id, CallLog.status == "completed")
        .scalar()
        or 0
    )
    response_rate = round(completed / total * 100, 1) if total else 0.0

    # Per-question breakdown
    questions = (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index)
        .all()
    )
    questions_summary = []
    for q in questions:
        answers = (
            db.query(Answer.normalized_value)
            .filter(Answer.campaign_id == campaign_id, Answer.question_key == q.key)
            .all()
        )
        counts = Counter(a[0] for a in answers if a[0])
        total_answers = sum(counts.values())
        distribution = [
            {
                "label": label,
                "count": count,
                "percent": round(count / total_answers * 100, 1),
            }
            for label, count in counts.most_common()
        ]
        questions_summary.append(
            {
                "question_id": q.id,
                "question_key": q.key,
                "prompt": q.prompt,
                "question_type": q.question_type,
                "total_answers": total_answers,
                "distribution": distribution,
            }
        )

    return {
        "campaign_id": campaign_id,
        "total_sessions": total,
        "completed_sessions": completed,
        "response_rate_percent": response_rate,
        "questions": questions_summary,
    }


@router.get("/questions/{question_key}")
def question_analytics(campaign_id: int, question_key: str, db: Session = Depends(get_db)):
    """
    Detailed answer distribution for a single question — ready for a bar/pie chart.
    Returns: { labels: [...], counts: [...], percents: [...] }
    """
    _get_campaign_or_404(campaign_id, db)

    answers = (
        db.query(Answer.normalized_value)
        .filter(Answer.campaign_id == campaign_id, Answer.question_key == question_key)
        .all()
    )
    if not answers:
        raise HTTPException(status_code=404, detail="No answers found for this question")

    counts = Counter(a[0] for a in answers if a[0])
    total = sum(counts.values())
    ordered = counts.most_common()

    return {
        "campaign_id": campaign_id,
        "question_key": question_key,
        "total_answers": total,
        "labels": [item[0] for item in ordered],
        "counts": [item[1] for item in ordered],
        "percents": [round(item[1] / total * 100, 1) for item in ordered],
    }


@router.get("/responses")
def list_responses(campaign_id: int, db: Session = Depends(get_db)):
    """
    Full table of all Answer rows for this campaign — for data export / drill-down.
    """
    _get_campaign_or_404(campaign_id, db)

    rows = (
        db.query(Answer)
        .filter(Answer.campaign_id == campaign_id)
        .order_by(Answer.created_at.desc())
        .limit(1000)
        .all()
    )
    return {
        "campaign_id": campaign_id,
        "count": len(rows),
        "answers": [
            {
                "session_id": r.session_id,
                "question_key": r.question_key,
                "raw_text": r.raw_text,
                "normalized_value": r.normalized_value,
                "answer_type": r.answer_type,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
