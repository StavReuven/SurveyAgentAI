"""SAA-99 Dashboard + Live Calls — API router.

Stories implemented:
  SAA-100 KPI Dashboard Cards
    SAA-101 metrics endpoints
    SAA-103 filters (campaign_id, period)
  SAA-104 Charts
    SAA-105 aggregation queries (outcomes + calls-by-hour)
  SAA-108 Live Calls List
    SAA-109 live endpoint
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CallLog, Campaign, Participant

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ── shared live-session registry (injected from main at startup) ─────────────
_live_sessions: dict[str, dict] = {}


def set_live_sessions_store(store: dict[str, dict]) -> None:
    """Called once from main.py to wire the in-memory session dict."""
    global _live_sessions
    _live_sessions = store


# ── helpers ──────────────────────────────────────────────────────────────────

def _period_start(period: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return None  # "all"


def _base_query(db: Session, campaign_id: int | None, period: str):
    q = db.query(CallLog)
    if campaign_id:
        q = q.filter(CallLog.campaign_id == campaign_id)
    since = _period_start(period)
    if since:
        q = q.filter(CallLog.started_at >= since)
    return q


# ── SAA-101: KPI metrics endpoint ────────────────────────────────────────────

@router.get("/kpis")
def get_kpis(
    campaign_id: int | None = Query(default=None),
    period: str = Query(default="all", pattern="^(today|week|month|all)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """SAA-101 — Return KPI card values with optional campaign + period filters."""
    q = _base_query(db, campaign_id, period)

    total_calls   = q.count()
    completed     = q.filter(CallLog.status == "completed").count()
    failed        = q.filter(CallLog.status == "failed").count()
    escalated     = q.filter(CallLog.status == "escalated").count()
    not_now       = q.filter(CallLog.status == "not_now").count()
    active_db     = q.filter(CallLog.status == "active").count()

    # live active calls from in-memory store (SAA-109 source of truth)
    live_count = sum(
        1 for s in _live_sessions.values()
        if (campaign_id is None or s.get("campaign_id") == campaign_id)
        and not s.get("completed_at")
    )

    completion_rate = round(completed / total_calls * 100, 1) if total_calls else 0.0

    # Average rapport score (avg confidence from completed calls)
    avg_rapport = (
        db.query(func.avg(CallLog.rapport_score))
        .filter(CallLog.rapport_score.isnot(None))
        .scalar()
    )

    # Average rating answer (from answers JSON — extracts numeric values)
    all_ratings: list[float] = []
    logs_with_answers = (
        db.query(CallLog.answers)
        .filter(CallLog.answers.isnot(None))
        .filter(CallLog.status == "completed")
        .all()
    )
    for (answers,) in logs_with_answers:
        if isinstance(answers, dict):
            for v in answers.values():
                try:
                    n = float(v)
                    if 1 <= n <= 10:
                        all_ratings.append(n)
                except (ValueError, TypeError):
                    pass
    avg_rating = round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else None

    # Average call duration in seconds (completed calls with ended_at set)
    avg_duration_seconds = (
        db.query(
            func.avg(func.extract("epoch", CallLog.ended_at - CallLog.started_at))
        )
        .filter(CallLog.ended_at.isnot(None))
        .scalar()
    )

    # Campaign counts (SAA-103 filter context)
    active_campaigns = db.query(func.count(Campaign.id)).filter(Campaign.status == "active").scalar()
    total_participants = db.query(func.count(Participant.id)).scalar()

    return {
        "total_calls": total_calls,
        "completed": completed,
        "failed": failed,
        "escalated": escalated,
        "not_now": not_now,
        "active_calls": live_count,
        "completion_rate": completion_rate,
        "avg_rapport_score": round(avg_rapport, 2) if avg_rapport else None,
        "avg_rating": avg_rating,
        "avg_duration_seconds": round(avg_duration_seconds) if avg_duration_seconds else None,
        "active_campaigns": active_campaigns,
        "total_participants": total_participants,
        "period": period,
        "campaign_id": campaign_id,
    }


# ── SAA-105: Call outcomes aggregation ───────────────────────────────────────

@router.get("/charts/outcomes")
def get_call_outcomes(
    campaign_id: int | None = Query(default=None),
    period: str = Query(default="all", pattern="^(today|week|month|all)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """SAA-105 — Aggregate call counts grouped by outcome status (for pie chart)."""
    q = _base_query(db, campaign_id, period)

    rows = (
        q.with_entities(CallLog.status, func.count(CallLog.id).label("count"))
        .group_by(CallLog.status)
        .all()
    )
    counts = {r.status: r.count for r in rows}

    # Add live active sessions not yet in DB
    live_active = sum(
        1 for s in _live_sessions.values()
        if (campaign_id is None or s.get("campaign_id") == campaign_id)
        and not s.get("completed_at")
    )
    counts["active"] = counts.get("active", 0) + live_active

    labels = ["completed", "active", "not_now", "failed", "escalated"]
    return {
        "labels": labels,
        "data": [counts.get(l, 0) for l in labels],
        "colors": ["#4ade80", "#38bdf8", "#fbbf24", "#f87171", "#a78bfa"],
    }


# ── SAA-105: Calls by hour aggregation ───────────────────────────────────────

@router.get("/charts/calls-by-hour")
def get_calls_by_hour(
    campaign_id: int | None = Query(default=None),
    period: str = Query(default="today", pattern="^(today|week|month|all)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """SAA-105 — Calls started per hour bucket (for bar chart)."""
    q = _base_query(db, campaign_id, period)

    rows = (
        q.with_entities(
            func.to_char(CallLog.started_at, "HH24:00").label("hour"),
            func.count(CallLog.id).label("count"),
        )
        .group_by("hour")
        .order_by("hour")
        .all()
    )

    labels = [r.hour for r in rows]
    data   = [r.count for r in rows]

    if not labels:
        # Return 24-hour empty skeleton so chart renders
        labels = [f"{h:02d}:00" for h in range(24)]
        data   = [0] * 24

    return {"labels": labels, "data": data}


# ── SAA-109: Live calls endpoint ─────────────────────────────────────────────

@router.get("/live-calls")
def get_live_calls(
    campaign_id: int | None = Query(default=None),
) -> dict[str, Any]:
    """SAA-109 — Snapshot of all currently active voice sessions."""
    sessions = []
    for sid, s in _live_sessions.items():
        if campaign_id and s.get("campaign_id") != campaign_id:
            continue
        if s.get("completed_at"):
            continue
        ctx = s.get("ctx")
        if ctx is None:
            continue

        # Determine progress
        total_q = len(ctx.questions)
        answered = len(ctx.answers)
        progress_pct = round(answered / total_q * 100) if total_q else 0

        # Rapport = avg confidence from turn history
        confidences = [
            e["confidence"] for e in ctx.history
            if e.get("event") == "turn" and "confidence" in e and e["confidence"] > 0
        ]
        rapport = round(sum(confidences) / len(confidences), 2) if confidences else None

        sessions.append({
            "session_id": sid,
            "campaign_id": s.get("campaign_id"),
            "participant_phone": ctx.participant_phone,
            "current_state": str(ctx.state),
            "current_question_key": (ctx.current_question.question_key if ctx.current_question else None),
            "progress_pct": progress_pct,
            "questions_answered": answered,
            "total_questions": total_q,
            "turns": len(ctx.history),
            "rapport_score": rapport,
            "started_at": s.get("started_at"),
            "answers": dict(ctx.answers),
        })

    return {"live_calls": sessions, "count": len(sessions)}


# ── SAA-103: Campaign list for filter dropdown ────────────────────────────────

@router.get("/campaigns")
def get_campaigns_for_filter(db: Session = Depends(get_db)) -> list[dict]:
    """SAA-103 — Campaign list for dashboard filter dropdown."""
    campaigns = db.query(Campaign).order_by(Campaign.name).all()
    return [{"id": c.id, "name": c.name, "status": c.status} for c in campaigns]
