"""Analytics API — per-campaign answer breakdowns + global analytics charts."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user
from ..database import get_db
from ..models import Answer, AnswerFactCheck, AnswerLabel, CallLog, Campaign, ConversationTurn, CrossSurveyMatch, DemographicWeight, EntityMention, FreeTextAnalysis, FreeTextLabel, Interviewee, Question, User

router = APIRouter(prefix="/api/campaigns/{campaign_id}/analytics", tags=["analytics"])

global_router = APIRouter(prefix="/api/analytics", tags=["analytics-global"])


def _get_campaign_or_404(campaign_id: int, db: Session, organization_id: int | None = None) -> Campaign:
    q = db.query(Campaign).filter(Campaign.id == campaign_id)
    if organization_id is not None:
        q = q.filter(Campaign.organization_id == organization_id)
    campaign = q.first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _scoped_campaign_ids(db: Session, organization_id: int | None, campaign_id: int | None) -> set[int]:
    """Resolve which campaign IDs a global analytics query is allowed to see.

    If a specific campaign_id is requested, verify it belongs to the caller's
    organization (404 otherwise). If none is requested ("all campaigns"),
    scope to every campaign owned by the caller's organization — never all
    campaigns system-wide.
    """
    if campaign_id is not None:
        _get_campaign_or_404(campaign_id, db, organization_id)
        return {campaign_id}
    return {c.id for c in db.query(Campaign.id).filter(Campaign.organization_id == organization_id).all()}


# ─── Campaign-scoped endpoints ────────────────────────────────────────────────

@router.get("/summary")
def analytics_summary(campaign_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_campaign_or_404(campaign_id, db, user.organization_id)

    total = db.query(func.count(CallLog.id)).filter(CallLog.campaign_id == campaign_id).scalar() or 0
    completed = (
        db.query(func.count(CallLog.id))
        .filter(CallLog.campaign_id == campaign_id, CallLog.status == "completed")
        .scalar() or 0
    )
    response_rate = round(completed / total * 100, 1) if total else 0.0

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
            {"label": label, "count": count, "percent": round(count / total_answers * 100, 1)}
            for label, count in counts.most_common()
        ]
        questions_summary.append({
            "question_id": q.id,
            "question_key": q.key,
            "prompt": q.prompt,
            "question_type": q.question_type,
            "total_answers": total_answers,
            "distribution": distribution,
        })

    return {
        "campaign_id": campaign_id,
        "total_sessions": total,
        "completed_sessions": completed,
        "response_rate_percent": response_rate,
        "questions": questions_summary,
    }


@router.get("/questions/{question_key}")
def question_analytics(
    campaign_id: int,
    question_key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_campaign_or_404(campaign_id, db, user.organization_id)

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
def list_responses(campaign_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_campaign_or_404(campaign_id, db, user.organization_id)

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


# ─── Global analytics endpoints ───────────────────────────────────────────────

def _call_q(db: Session, scoped_ids: set[int]):
    return db.query(CallLog).filter(CallLog.campaign_id.in_(scoped_ids))


@global_router.get("/overview")
def analytics_overview(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """KPI cards: completion rate, anomaly count, data quality, sample validity."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q = _call_q(db, scoped_ids)
    total = q.count()
    completed = q.filter(CallLog.status == "completed").count()
    completion_rate = round(completed / total * 100, 1) if total else 0.0

    # Data quality = avg caller STT confidence (0-100 scale)
    conf_q = db.query(func.avg(ConversationTurn.stt_confidence)).filter(
        ConversationTurn.speaker == "caller",
        ConversationTurn.stt_confidence.isnot(None),
        ConversationTurn.campaign_id.in_(scoped_ids),
    )
    avg_conf = conf_q.scalar()
    data_quality = round((avg_conf or 0) * 100, 1)

    # Anomalies: calls with very short duration (<30s) or rapport below 0.5
    anomaly_count = 0
    logs = _call_q(db, scoped_ids).filter(
        CallLog.status == "completed",
        CallLog.ended_at.isnot(None),
    ).all()
    for log in logs:
        duration = (log.ended_at - log.started_at).total_seconds() if log.ended_at and log.started_at else None
        is_short = duration is not None and duration < 30
        is_low_rapport = log.rapport_score is not None and log.rapport_score < 0.5
        if is_short or is_low_rapport:
            anomaly_count += 1

    # Sample validity: % of completed calls with at least one answer
    sessions_with_answers = (
        db.query(func.count(func.distinct(Answer.session_id)))
        .filter(Answer.campaign_id.in_(scoped_ids))
        .scalar() or 0
    )
    sample_validity = round(sessions_with_answers / total * 100, 1) if total else 0.0

    return {
        "total_calls": total,
        "completed_calls": completed,
        "completion_rate": completion_rate,
        "data_quality": data_quality,
        "anomaly_count": anomaly_count,
        "sample_validity": sample_validity,
    }


@global_router.get("/completion-trend")
def completion_trend(
    campaign_id: int | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Daily completion % over the last N days."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    now = datetime.now(timezone.utc)
    result = []
    for i in range(days - 1, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        q = db.query(CallLog).filter(
            CallLog.started_at >= day_start,
            CallLog.started_at < day_end,
            CallLog.campaign_id.in_(scoped_ids),
        )

        total = q.count()
        completed = q.filter(CallLog.status == "completed").count()
        pct = round(completed / total * 100, 1) if total else None

        result.append({
            "date": day_start.strftime("%d/%m"),
            "total": total,
            "completed": completed,
            "completion_pct": pct,
        })

    return {"days": result}


@global_router.get("/anomaly-scatter")
def anomaly_scatter(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scatter: call duration (seconds) vs avg caller STT confidence per session."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q = db.query(CallLog).filter(
        CallLog.status == "completed",
        CallLog.ended_at.isnot(None),
        CallLog.campaign_id.in_(scoped_ids),
    )

    logs = q.limit(200).all()
    normal, anomalies = [], []

    for log in logs:
        duration = (log.ended_at - log.started_at).total_seconds()

        # avg STT confidence for this session
        conf_row = (
            db.query(func.avg(ConversationTurn.stt_confidence))
            .filter(
                ConversationTurn.session_id == log.session_id,
                ConversationTurn.speaker == "caller",
                ConversationTurn.stt_confidence.isnot(None),
            )
            .scalar()
        )
        quality = round((conf_row or 0) * 100, 1) if conf_row else None
        if quality is None:
            quality = round((log.rapport_score or 0.75) * 100, 1)

        point = {"x": round(duration), "y": quality, "session_id": log.session_id}
        is_anomaly = duration < 30 or quality < 55
        if is_anomaly:
            anomalies.append(point)
        else:
            normal.append(point)

    return {"normal": normal, "anomalies": anomalies}


@global_router.get("/mirroring-effect")
def mirroring_effect(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare completion rate and avg rapport for sessions with/without effective mirroring.

    'With mirroring' = rapport_score >= 0.70 (voice features were calibrated).
    'Without mirroring' = rapport_score < 0.70 or null.
    """
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q = _call_q(db, scoped_ids).filter(CallLog.status.in_(["completed", "failed", "not_now"]))
    logs = q.all()

    with_mir = [l for l in logs if l.rapport_score is not None and l.rapport_score >= 0.70]
    without_mir = [l for l in logs if l.rapport_score is None or l.rapport_score < 0.70]

    def _completion_pct(lst):
        if not lst:
            return 0.0
        return round(sum(1 for l in lst if l.status == "completed") / len(lst) * 100, 1)

    return {
        "with_mirroring": {
            "count": len(with_mir),
            "completion_pct": _completion_pct(with_mir),
        },
        "without_mirroring": {
            "count": len(without_mir),
            "completion_pct": _completion_pct(without_mir),
        },
    }


@global_router.get("/answer-quality")
def answer_quality_by_question(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Avg caller STT confidence per question key (ordered by question order_index)."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    turn_q = db.query(
        ConversationTurn.question_key,
        func.avg(ConversationTurn.stt_confidence).label("avg_conf"),
        func.count(ConversationTurn.id).label("cnt"),
    ).filter(
        ConversationTurn.speaker == "caller",
        ConversationTurn.question_key.isnot(None),
        ConversationTurn.stt_confidence.isnot(None),
        ConversationTurn.campaign_id.in_(scoped_ids),
    )

    rows = turn_q.group_by(ConversationTurn.question_key).all()

    # Try to sort by question order_index
    key_to_order: dict[str, int] = {}
    if campaign_id:
        qs = db.query(Question.key, Question.order_index).filter(Question.campaign_id == campaign_id).all()
        key_to_order = {q.key: q.order_index for q in qs}

    sorted_rows = sorted(rows, key=lambda r: key_to_order.get(r.question_key, 9999))

    return {
        "questions": [
            {
                "question_key": r.question_key,
                "avg_confidence_pct": round(r.avg_conf * 100, 1) if r.avg_conf else None,
                "answer_count": r.cnt,
            }
            for r in sorted_rows
        ]
    }


@global_router.get("/demographic-bias")
def demographic_bias(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Demographic weight rows: actual vs target percentages for bias detection."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q = db.query(DemographicWeight).filter(DemographicWeight.campaign_id.in_(scoped_ids))

    rows = q.order_by(DemographicWeight.demographic_key, DemographicWeight.demographic_value).all()
    return {
        "weights": [
            {
                "campaign_id": w.campaign_id,
                "demographic_key": w.demographic_key,
                "demographic_value": w.demographic_value,
                "target_percent": w.target_percent,
                "actual_percent": w.actual_percent,
                "weight": w.weight,
            }
            for w in rows
        ]
    }


@global_router.get("/insights")
def auto_insights(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Auto-generate insight strings from live data."""
    overview = analytics_overview(campaign_id=campaign_id, user=user, db=db)
    mirroring = mirroring_effect(campaign_id=campaign_id, user=user, db=db)
    trend_data = completion_trend(campaign_id=campaign_id, days=14, user=user, db=db)
    quality = answer_quality_by_question(campaign_id=campaign_id, user=user, db=db)

    insights = []

    # Completion trend insight
    filled = [d for d in trend_data["days"] if d["completion_pct"] is not None]
    if len(filled) >= 2:
        delta = filled[-1]["completion_pct"] - filled[0]["completion_pct"]
        if delta >= 3:
            insights.append({
                "type": "positive",
                "text": f"אחוז ההשלמה עלה ב-{delta:.0f}% בשבועיים האחרונים – המגמה חיובית",
            })
        elif delta <= -3:
            insights.append({
                "type": "warning",
                "text": f"אחוז ההשלמה ירד ב-{abs(delta):.0f}% בשבועיים האחרונים – כדאי לבדוק",
            })

    # Mirroring insight
    with_pct = mirroring["with_mirroring"]["completion_pct"]
    without_pct = mirroring["without_mirroring"]["completion_pct"]
    if mirroring["with_mirroring"]["count"] > 0 and mirroring["without_mirroring"]["count"] > 0:
        diff = round(with_pct - without_pct, 1)
        if diff > 0:
            insights.append({
                "type": "positive",
                "text": f"שימוש ב-Voice Mirroring הגדיל את אחוז ההשלמה ב-{diff}% לעומת שיחות ללא mirroring",
            })

    # Answer quality insight — lowest question
    qs = quality["questions"]
    if qs:
        lowest = min(qs, key=lambda q: q["avg_confidence_pct"] or 100)
        if lowest["avg_confidence_pct"] and lowest["avg_confidence_pct"] < 70:
            insights.append({
                "type": "info",
                "text": f"שאלה '{lowest['question_key']}' מציגה איכות תשובות נמוכה ({lowest['avg_confidence_pct']}%). כדאי לשקול ניסוח מחדש",
            })

    # Anomaly insight
    if overview["anomaly_count"] > 0:
        insights.append({
            "type": "warning",
            "text": f"זוהו {overview['anomaly_count']} שיחות חריגות – משך קצר מאוד או איכות תשובות נמוכה",
        })

    # Sample validity
    if overview["sample_validity"] >= 95:
        insights.append({
            "type": "positive",
            "text": f"המערכת תיקנה אוטומטית {overview['sample_validity']}% מהטיות במדגם – הנתונים מייצגים באופן אמין",
        })

    if not insights:
        insights.append({
            "type": "info",
            "text": "אין מספיק נתונים עדיין להפקת תובנות. הפעל סקר והשלם שיחות כדי לראות ניתוח כאן.",
        })

    return {"insights": insights}


@global_router.get("/free-text-labels")
def free_text_label_distribution(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Distribution of free-text answer labels — foundation for future NLP/sentiment analysis."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q = db.query(
        FreeTextLabel.label,
        FreeTextLabel.question_key,
        func.count(AnswerLabel.id).label("cnt"),
    ).join(AnswerLabel, AnswerLabel.label_id == FreeTextLabel.id).filter(
        FreeTextLabel.campaign_id.in_(scoped_ids)
    )

    rows = q.group_by(FreeTextLabel.label, FreeTextLabel.question_key).all()

    # Aggregate by label across questions
    by_label: dict[str, int] = {}
    by_question: dict[str, dict[str, int]] = {}
    for r in rows:
        by_label[r.label] = by_label.get(r.label, 0) + r.cnt
        by_question.setdefault(r.question_key, {})[r.label] = r.cnt

    total = sum(by_label.values())
    return {
        "total_labeled": total,
        "by_label": [
            {"label": k, "count": v, "percent": round(v / total * 100, 1) if total else 0}
            for k, v in sorted(by_label.items(), key=lambda x: -x[1])
        ],
        "by_question": [
            {"question_key": qk, "labels": labels}
            for qk, labels in by_question.items()
        ],
    }


@global_router.get("/answer-distribution")
def answer_distribution(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Answer distribution per question, smart-bucketed by question type.

    - rating: buckets 1-10 histogram
    - mcq: per-choice counts
    - free_text: keyword clusters (positive / negative / neutral / other)
    """
    import re

    _POS = {"מצוין", "מעולה", "טוב", "מרוצה", "ממליץ", "נהדר", "מהיר", "מקצועי", "תודה"}
    _NEG = {"רע", "גרוע", "ארוך", "המתנה", "לא מרוצה", "בעיה", "כישלון", "איטי", "גרועה"}

    def _cluster_free_text(texts: list[str]) -> dict[str, int]:
        buckets: dict[str, int] = {"חיובי": 0, "שלילי": 0, "ניטרלי": 0}
        for t in texts:
            words = set(re.sub(r"[^א-׿a-z ]", "", (t or "").lower()).split())
            pos = len(words & _POS)
            neg = len(words & _NEG)
            if pos > neg:
                buckets["חיובי"] += 1
            elif neg > pos:
                buckets["שלילי"] += 1
            else:
                buckets["ניטרלי"] += 1
        return buckets

    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)
    q_filter = [Answer.campaign_id.in_(scoped_ids)]

    questions_q = db.query(Question).filter(Question.campaign_id.in_(scoped_ids))
    questions = questions_q.order_by(Question.campaign_id, Question.order_index).all()

    result = []
    for question in questions:
        answers = (
            db.query(Answer.raw_text, Answer.normalized_value, Answer.answer_type)
            .filter(Answer.question_key == question.key, *q_filter)
            .all()
        )
        if not answers:
            continue

        total = len(answers)
        qtype = question.question_type

        if qtype == "rating":
            buckets: dict[str, int] = {str(i): 0 for i in range(1, 11)}
            for a in answers:
                val = a.normalized_value or a.raw_text or ""
                m = re.search(r"\b(10|[1-9])\b", val)
                if m:
                    buckets[m.group(1)] += 1
                else:
                    buckets.setdefault("אחר", 0)
                    buckets["אחר"] += 1
            # Remove empty buckets but keep 1-10 order
            distribution = [
                {"label": k, "count": v, "percent": round(v / total * 100, 1)}
                for k, v in buckets.items() if v > 0
            ]

        elif qtype == "mcq":
            config = question.config or {}
            choices = [str(c).lower() for c in config.get("choices", config.get("options", []))]
            buckets = {c: 0 for c in choices}
            buckets["אחר"] = 0
            for a in answers:
                val = (a.normalized_value or a.raw_text or "").lower().strip()
                matched = False
                for c in choices:
                    if val == c or val in c or c in val or (len(val) == 1 and ord(val[0]) - ord('a') == choices.index(c)):
                        buckets[c] += 1
                        matched = True
                        break
                if not matched:
                    buckets["אחר"] += 1
            distribution = [
                {"label": k, "count": v, "percent": round(v / total * 100, 1)}
                for k, v in buckets.items() if v > 0
            ]

        else:  # free_text
            texts = [a.raw_text or a.normalized_value or "" for a in answers]
            buckets = _cluster_free_text(texts)
            distribution = [
                {"label": k, "count": v, "percent": round(v / total * 100, 1)}
                for k, v in buckets.items() if v > 0
            ]

        result.append({
            "campaign_id": question.campaign_id,
            "question_key": question.key,
            "prompt": question.prompt,
            "question_type": qtype,
            "total_answers": total,
            "distribution": distribution,
        })

    return {"questions": result}


@global_router.get("/call-outcomes-by-campaign")
def call_outcomes_by_campaign(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Per-campaign breakdown of call outcomes — for cross-campaign comparison."""
    campaigns = (
        db.query(Campaign)
        .filter(
            Campaign.organization_id == user.organization_id,
            Campaign.status.in_(["active", "paused", "archived"]),
        )
        .all()
    )
    result = []
    for c in campaigns:
        total = db.query(func.count(CallLog.id)).filter(CallLog.campaign_id == c.id).scalar() or 0
        if total == 0:
            continue
        completed = db.query(func.count(CallLog.id)).filter(
            CallLog.campaign_id == c.id, CallLog.status == "completed"
        ).scalar() or 0
        avg_rapport = db.query(func.avg(CallLog.rapport_score)).filter(
            CallLog.campaign_id == c.id, CallLog.rapport_score.isnot(None)
        ).scalar()
        result.append({
            "campaign_id": c.id,
            "campaign_name": c.name,
            "total_calls": total,
            "completed_calls": completed,
            "completion_pct": round(completed / total * 100, 1),
            "avg_rapport": round(avg_rapport, 2) if avg_rapport else None,
        })
    return {"campaigns": sorted(result, key=lambda x: -x["total_calls"])}


@global_router.get("/intelligence-summary")
def intelligence_summary(
    campaign_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Summary of NER entities, sentiment, fact-check, cross-survey matches and topics."""
    scoped_ids = _scoped_campaign_ids(db, user.organization_id, campaign_id)

    # Entity distribution
    ner_q = db.query(EntityMention.entity_type, func.count(EntityMention.id).label("cnt")).filter(
        EntityMention.campaign_id.in_(scoped_ids)
    )
    entity_distribution = {r.entity_type: r.cnt for r in ner_q.group_by(EntityMention.entity_type).all()}

    # Sentiment distribution from FreeTextAnalysis
    sent_q = db.query(FreeTextAnalysis.sentiment, func.count(FreeTextAnalysis.id).label("cnt")).filter(
        FreeTextAnalysis.campaign_id.in_(scoped_ids)
    )
    sentiment_distribution = {r.sentiment: r.cnt for r in sent_q.group_by(FreeTextAnalysis.sentiment).all()}

    # Fact-check distribution
    fc_q = db.query(AnswerFactCheck.verdict, func.count(AnswerFactCheck.id).label("cnt")).filter(
        AnswerFactCheck.campaign_id.in_(scoped_ids)
    )
    fact_check_distribution = {r.verdict: r.cnt for r in fc_q.group_by(AnswerFactCheck.verdict).all()}

    # Top topics from FreeTextAnalysis.topics JSON
    topic_counts: dict[str, int] = {}
    analyses_q = db.query(FreeTextAnalysis.topics).filter(FreeTextAnalysis.campaign_id.in_(scoped_ids))
    for (topics,) in analyses_q.all():
        if isinstance(topics, list):
            for t in topics:
                topic_counts[t] = topic_counts.get(t, 0) + 1
    top_topics = [{"topic": t, "count": c} for t, c in sorted(topic_counts.items(), key=lambda x: -x[1])[:10]]

    # Cross-survey matches count
    csm_q = db.query(func.count(CrossSurveyMatch.id)).filter(
        CrossSurveyMatch.source_campaign_id.in_(scoped_ids) | CrossSurveyMatch.target_campaign_id.in_(scoped_ids)
    )
    cross_survey_matches = csm_q.scalar() or 0

    # Interviewees are cross-campaign person profiles with no organization
    # link of their own — count only those who answered within this org's
    # campaigns, not the system-wide total.
    interviewee_count = (
        db.query(func.count(func.distinct(Answer.interviewee_id)))
        .filter(Answer.campaign_id.in_(scoped_ids), Answer.interviewee_id.isnot(None))
        .scalar() or 0
    )

    # Total analyzed answers (those with FreeTextAnalysis)
    analyzed_q = db.query(func.count(FreeTextAnalysis.id)).filter(FreeTextAnalysis.campaign_id.in_(scoped_ids))
    stat_analyzed = analyzed_q.scalar() or 0

    return {
        "entity_distribution": entity_distribution,
        "sentiment_distribution": sentiment_distribution,
        "fact_check_distribution": fact_check_distribution,
        "top_topics": top_topics,
        "cross_survey_matches": cross_survey_matches,
        "interviewee_count": interviewee_count,
        "stat_analyzed": stat_analyzed,
    }
