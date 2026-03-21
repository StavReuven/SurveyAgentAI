import asyncio
import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import BranchRule, CallLog, Campaign, Participant, Question
from .voice.dialogue.fsm import QuestionContext
from .voice.pipeline import VoicePipeline
from .dashboard.router import router as dashboard_router, set_live_sessions_store
from .schemas import (
    CampaignCreate,
    CampaignOut,
    CampaignSummary,
    CampaignUpdate,
    ParticipantOut,
    QuestionCreate,
    QuestionOut,
    QuestionReorder,
    QuestionUpdate,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="VoiceSurvey AI Campaign Builder", version="0.1.0")
app.include_router(dashboard_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/voice")
def voice_simulator():
    return FileResponse("app/static/voice.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "campaign-builder"}


@app.post("/api/campaigns", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    campaign = Campaign(**payload.model_dump())
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@app.get("/api/campaigns", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return db.query(Campaign).order_by(Campaign.created_at.desc()).all()


@app.get("/api/campaigns/summary", response_model=list[CampaignSummary])
def list_campaign_summaries(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Campaign.id,
            Campaign.name,
            Campaign.language,
            Campaign.timezone,
            Campaign.status,
            func.count(func.distinct(Question.id)).label("question_count"),
            func.count(func.distinct(Participant.id)).label("participant_count"),
        )
        .outerjoin(Question, Question.campaign_id == Campaign.id)
        .outerjoin(Participant, Participant.campaign_id == Campaign.id)
        .group_by(Campaign.id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return [CampaignSummary.model_validate(dict(row._mapping)) for row in rows]


@app.get("/api/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@app.put("/api/campaigns/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, payload: CampaignUpdate, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(campaign, key, value)
    campaign.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(campaign)
    return campaign


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(campaign)
    db.commit()
    return {"deleted": True}


@app.post("/api/campaigns/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    clone = Campaign(
        name=f"{campaign.name} (Copy)",
        language=campaign.language,
        timezone=campaign.timezone,
        consent_text=campaign.consent_text,
        status="draft",
    )
    db.add(clone)
    db.flush()

    id_map = {}
    for question in sorted(campaign.questions, key=lambda q: q.order_index):
        new_question = Question(
            campaign_id=clone.id,
            order_index=question.order_index,
            key=f"{question.key}_copy_{clone.id}",
            prompt=question.prompt,
            question_type=question.question_type,
            required=question.required,
            config=question.config,
        )
        db.add(new_question)
        db.flush()
        id_map[question.id] = new_question.id

    for rule in campaign.rules:
        db.add(
            BranchRule(
                campaign_id=clone.id,
                source_question_id=id_map.get(rule.source_question_id, rule.source_question_id),
                operator=rule.operator,
                value=rule.value,
                action=rule.action,
                target_question_id=(
                    id_map.get(rule.target_question_id)
                    if rule.target_question_id is not None
                    else None
                ),
                priority=rule.priority,
            )
        )

    db.commit()
    db.refresh(clone)
    return clone


@app.post("/api/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "paused"
    db.commit()
    db.refresh(campaign)
    return campaign


@app.post("/api/campaigns/{campaign_id}/resume", response_model=CampaignOut)
def resume_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "active"
    db.commit()
    db.refresh(campaign)
    return campaign


@app.post("/api/campaigns/{campaign_id}/questions", response_model=QuestionOut)
def create_question(campaign_id: int, payload: QuestionCreate, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")

    max_order = (
        db.query(func.max(Question.order_index))
        .filter(Question.campaign_id == campaign_id)
        .scalar()
    )
    question = Question(
        campaign_id=campaign_id,
        order_index=(max_order or 0) + 1,
        **payload.model_dump(),
    )
    db.add(question)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate question key") from exc

    db.refresh(question)
    return question


@app.get("/api/campaigns/{campaign_id}/questions", response_model=list[QuestionOut])
def list_questions(campaign_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index.asc())
        .all()
    )


@app.put("/api/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: int, payload: QuestionUpdate, db: Session = Depends(get_db)):
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(question, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate question key") from exc
    db.refresh(question)
    return question


@app.delete("/api/questions/{question_id}")
def delete_question(question_id: int, db: Session = Depends(get_db)):
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    campaign_id = question.campaign_id
    db.delete(question)
    db.commit()

    # Compact ordering after deletion.
    questions = (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index)
        .all()
    )
    for idx, item in enumerate(questions, start=1):
        item.order_index = idx
    db.commit()
    return {"deleted": True}


@app.post("/api/campaigns/{campaign_id}/questions/reorder")
def reorder_questions(campaign_id: int, payload: QuestionReorder, db: Session = Depends(get_db)):
    questions = (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index)
        .all()
    )
    existing = {q.id for q in questions}
    incoming = payload.question_ids
    if existing != set(incoming):
        raise HTTPException(status_code=400, detail="question_ids must match campaign questions")

    for idx, question_id in enumerate(incoming, start=1):
        db.get(Question, question_id).order_index = idx
    db.commit()
    return {"reordered": True}


@app.post("/api/campaigns/{campaign_id}/rules", response_model=RuleOut)
def create_rule(campaign_id: int, payload: RuleCreate, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")

    question_ids = {
        row[0] for row in db.query(Question.id).filter(Question.campaign_id == campaign_id).all()
    }
    if payload.source_question_id not in question_ids:
        raise HTTPException(status_code=400, detail="source_question_id is invalid for this campaign")
    if payload.target_question_id and payload.target_question_id not in question_ids:
        raise HTTPException(status_code=400, detail="target_question_id is invalid for this campaign")

    rule = BranchRule(campaign_id=campaign_id, **payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@app.get("/api/campaigns/{campaign_id}/rules", response_model=list[RuleOut])
def list_rules(campaign_id: int, db: Session = Depends(get_db)):
    return (
        db.query(BranchRule)
        .filter(BranchRule.campaign_id == campaign_id)
        .order_by(BranchRule.priority.asc(), BranchRule.id.asc())
        .all()
    )


@app.put("/api/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(BranchRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(BranchRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"deleted": True}


@app.post("/api/campaigns/{campaign_id}/participants/upload")
async def upload_participants(
    campaign_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    required_columns = {"phone_number", "full_name", "locale"}
    missing_columns = required_columns.difference(reader.fieldnames or [])
    if missing_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Missing CSV columns: {', '.join(sorted(missing_columns))}",
        )

    inserted = 0
    skipped = 0
    existing_phones = {
        row[0]
        for row in (
            db.query(Participant.phone_number)
            .filter(Participant.campaign_id == campaign_id)
            .all()
        )
    }
    seen_phones = set(existing_phones)

    for row in reader:
        phone = (row.get("phone_number") or "").strip()
        if not phone:
            skipped += 1
            continue

        if phone in seen_phones:
            skipped += 1
            continue

        participant = Participant(
            campaign_id=campaign_id,
            phone_number=phone,
            full_name=(row.get("full_name") or "").strip() or None,
            locale=(row.get("locale") or "").strip() or None,
            opt_in=True,
            status="pending",
        )
        db.add(participant)
        seen_phones.add(phone)
        inserted += 1

    db.commit()
    return {"inserted": inserted, "skipped": skipped}


@app.get("/api/campaigns/{campaign_id}/participants", response_model=list[ParticipantOut])
def list_participants(campaign_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Participant)
        .filter(Participant.campaign_id == campaign_id)
        .order_by(Participant.id.desc())
        .all()
    )


# ===========================================================================
# SAA-50: Voice AI Pipeline endpoints
# ===========================================================================

# In-memory session store (keyed by session_id).
_voice_sessions: dict[str, dict] = {}

_pipeline = VoicePipeline()

# Wire session store into dashboard router so live-calls endpoint can read it.
set_live_sessions_store(_voice_sessions)


class VoiceSessionStartRequest(BaseModel):
    participant_phone: str
    locale: str | None = None


class VoiceTurnRequest(BaseModel):
    transcript: str          # text from caller (or forwarded from STT adapter)
    audio_duration_ms: float = 0.0


def _load_question_contexts(campaign_id: int, db: Session) -> list[QuestionContext]:
    questions = (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index.asc())
        .all()
    )
    return [
        QuestionContext(
            question_id=q.id,
            question_key=q.key,
            prompt=q.prompt,
            question_type=q.question_type,
            order_index=q.order_index,
            config=q.config or {},
        )
        for q in questions
    ]


@app.post("/api/campaigns/{campaign_id}/voice/sessions")
async def start_voice_session(
    campaign_id: int,
    payload: VoiceSessionStartRequest,
    db: Session = Depends(get_db),
):
    """SAA-50/SAA-51: Start a new voice survey session for a campaign participant."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "active":
        raise HTTPException(status_code=400, detail="Campaign is not active")

    question_contexts = _load_question_contexts(campaign_id, db)
    if not question_contexts:
        raise HTTPException(status_code=400, detail="Campaign has no questions")

    # Load branch rules and convert to plain objects so they survive session close
    _branch_rule_rows = (
        db.query(BranchRule)
        .filter(BranchRule.campaign_id == campaign_id)
        .order_by(BranchRule.priority.asc())
        .all()
    )
    branch_rules = [
        SimpleNamespace(
            source_question_id=r.source_question_id,
            target_question_id=r.target_question_id,
            operator=r.operator,
            value=r.value,
            action=r.action,
            priority=r.priority,
        )
        for r in _branch_rule_rows
    ]

    ctx = _pipeline.create_session(
        campaign_id=campaign_id,
        participant_phone=payload.participant_phone,
        questions=question_contexts,
        branch_rules=branch_rules,
        language=campaign.language,
        locale=payload.locale,
    )

    result = await _pipeline.start_session(ctx)

    _voice_sessions[ctx.session_id] = {
        "ctx": ctx,
        "campaign_id": campaign_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tts_metrics": [],
        "stt_metrics": [],
    }

    # SAA-101: persist call log for dashboard metrics
    call_log = CallLog(
        campaign_id=campaign_id,
        session_id=ctx.session_id,
        status="active",
        started_at=datetime.now(timezone.utc),
    )
    db.add(call_log)
    db.commit()

    return {
        "session_id": ctx.session_id,
        "response_text": result.response_text,
        "dialogue_action": result.dialogue_action,
        "current_state": result.current_state,
        "current_question_key": result.current_question_key,
        "session_complete": result.session_complete,
    }


@app.post("/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn")
async def process_voice_turn(
    campaign_id: int,
    session_id: str,
    payload: VoiceTurnRequest,
    db: Session = Depends(get_db),
):
    """SAA-50: Process one caller turn (transcript text → dialogue → TTS response)."""
    session = _voice_sessions.get(session_id)
    if not session or session["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Voice session not found")

    ctx = session["ctx"]

    # Wrap the provided transcript as an async generator of bytes so the
    # STT adapter's interface is satisfied (mock adapter ignores audio bytes
    # and uses preset responses; real adapters would receive actual audio).
    async def _text_as_audio_chunks():
        yield payload.transcript.encode()

    # Override mock STT to return the submitted transcript directly
    from .voice.stt.adapter import MockSTTAdapter
    _pipeline._stt = MockSTTAdapter(
        responses=[payload.transcript],
        confidence=0.92,
    )

    result = await _pipeline.process_turn(ctx, _text_as_audio_chunks())

    session["tts_metrics"].append(result.tts_metrics)
    session["stt_metrics"].append(result.stt_metrics)

    if result.session_complete:
        session["completed_at"] = datetime.now(timezone.utc).isoformat()
        session["answers"] = dict(ctx.answers)

    # SAA-101: update CallLog with final status + answers + rapport score
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log:
        call_log.turns_count = len(ctx.history)
        call_log.answers = dict(ctx.answers)
        # Rapport = avg turn confidence from history
        confidences = [
            e["confidence"] for e in ctx.history
            if e.get("event") == "turn" and "confidence" in e and e["confidence"] > 0
        ]
        call_log.rapport_score = round(sum(confidences) / len(confidences), 2) if confidences else None
        if result.session_complete:
            call_log.ended_at = datetime.now(timezone.utc)
            action = str(result.dialogue_action)
            if "escalat" in action:
                call_log.status = "escalated"
            elif "closing" in action or "end" in action:
                call_log.status = "not_now" if "not_now" in str(ctx.state) else "completed"
            else:
                call_log.status = "completed"
        db.commit()

    return {
        "session_id": session_id,
        "response_text": result.response_text,
        "dialogue_action": result.dialogue_action,
        "current_state": result.current_state,
        "current_question_key": result.current_question_key,
        "session_complete": result.session_complete,
        "stt_metrics": result.stt_metrics,
        "tts_metrics": result.tts_metrics,
    }


@app.get("/api/campaigns/{campaign_id}/voice/sessions/{session_id}")
def get_voice_session(campaign_id: int, session_id: str):
    """SAA-50: Get current state of a voice session."""
    session = _voice_sessions.get(session_id)
    if not session or session["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Voice session not found")

    ctx = session["ctx"]
    return {
        "session_id": session_id,
        "campaign_id": campaign_id,
        "participant_phone": ctx.participant_phone,
        "current_state": ctx.state,
        "current_question_index": ctx.current_question_index,
        "current_question_key": (ctx.current_question.question_key if ctx.current_question else None),
        "answers": dict(ctx.answers),
        "retry_count": ctx.retry_count,
        "started_at": session["started_at"],
        "completed_at": session.get("completed_at"),
        "tts_metrics_summary": session["tts_metrics"],
        "stt_metrics_summary": session["stt_metrics"],
        "history": ctx.history,
    }


@app.delete("/api/campaigns/{campaign_id}/voice/sessions/{session_id}")
def end_voice_session(campaign_id: int, session_id: str, db: Session = Depends(get_db)):
    """SAA-50: Terminate and discard a voice session."""
    session = _voice_sessions.pop(session_id, None)
    if not session or session["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Voice session not found")
    # Mark any still-active CallLog as failed
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log and call_log.status == "active":
        call_log.status = "failed"
        call_log.ended_at = datetime.now(timezone.utc)
        db.commit()
    return {"ended": True, "session_id": session_id}


@app.get("/dashboard")
def dashboard():
    return FileResponse("app/static/dashboard.html")
