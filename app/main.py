import asyncio
import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from collections import defaultdict

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .models import (
    Answer,
    AnswerFactCheck,
    AnswerLabel,
    BranchRule,
    CallLog,
    CallAttempt,
    CallingPolicy,
    Campaign,
    CampaignExecution,
    ConversationTurn,
    CrossSurveyMatch,
    DemographicWeight,
    EntityMention,
    FreeTextAnalysis,
    FreeTextLabel,
    Interviewee,
    Participant,
    Question,
)
from .voice.agent.service import AgentAIService
from .voice.dialogue.fsm import QuestionContext
from .voice.nlu.schema import IntentType
from .voice.mirroring.policy import MirroringPolicy, MirroringSettings
from .voice.escalation import get_escalation_queue
from .voice.pipeline import VoicePipeline
from .analytics.router import router as analytics_router, global_router as analytics_global_router
from .dashboard.router import router as dashboard_router, set_live_sessions_store
from .auth.deps import get_current_user
from .auth.router import router as auth_router
from .models import User
from .operator.router import router as operator_router
from .settings.router import router as settings_router
from .settings.dnc import router as settings_dnc_router
from .settings.audit import router as settings_audit_router
from .settings.dnc import is_blocked
from .telephony.gateway import get_gateway
from .telephony.router import set_voice_sessions_store
from .telephony.router import router as telephony_router
from .telephony.session_store import get_store as get_telephony_store
from .telephony.timeouts import start_watchdog
from .schemas import (
    CampaignCreate,
    CampaignExecutionOut,
    CampaignOut,
    CampaignSummary,
    CampaignUpdate,
    CallAttemptOut,
    CallingPolicyOut,
    CallingPolicyUpdate,
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None
_telephony_watchdog: asyncio.Task | None = None
_db_keepalive_task: asyncio.Task | None = None
_last_activity_at: datetime = datetime.now(timezone.utc)

# Neon (and similar serverless Postgres) suspends its compute after a few
# minutes of inactivity; the first query afterward pays a multi-second cold
# start. Pinging on a fixed timer would keep the compute alive 24/7 and burn
# through Neon's free-tier compute-hour quota in days, so this loop only
# pings while there has been *real* HTTP traffic recently — during genuine
# idle stretches (nights, weekends) it stays silent and lets Neon suspend
# normally to save quota.
DB_KEEPALIVE_INTERVAL_SECONDS = 60
DB_KEEPALIVE_ACTIVE_WINDOW_SECONDS = 600


async def _db_keepalive_loop() -> None:
    while True:
        await asyncio.sleep(DB_KEEPALIVE_INTERVAL_SECONDS)
        idle_seconds = (datetime.now(timezone.utc) - _last_activity_at).total_seconds()
        if idle_seconds > DB_KEEPALIVE_ACTIVE_WINDOW_SECONDS:
            continue
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            logger.warning("DB keep-alive ping failed", exc_info=True)


def _migrate_db() -> None:
    """Add columns that were introduced after initial schema creation."""
    new_cols = [
        ("call_logs",  "voice_metrics",    "JSON"),
        ("call_logs",  "history",          "JSON"),
        ("campaigns",  "description",      "TEXT"),
        ("campaigns",  "organization_id",  "INTEGER"),
        ("users",      "organization_id",  "INTEGER"),
        ("settings_audit_entries", "organization_id", "INTEGER"),
        ("call_attempts", "session_id", "VARCHAR(64)"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_cols:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                # Column already exists — ignore, but roll back first: on
                # Postgres a failed statement poisons the whole transaction,
                # so every later statement on this connection would silently
                # fail too (including genuinely new columns) without this.
                conn.rollback()

        # call_attempts.outcome/finished_at predate the async auto-dial
        # scheduler: an attempt now starts as "pending" (ringing/in-call) and
        # is only finalized once the call actually ends via the status
        # webhook, so finished_at must be nullable and the enum needs 'pending'.
        try:
            conn.execute(text("ALTER TYPE call_outcome ADD VALUE IF NOT EXISTS 'pending'"))
            conn.commit()
        except Exception:
            conn.rollback()  # not Postgres, or value already present
        try:
            conn.execute(text("ALTER TYPE call_outcome ADD VALUE IF NOT EXISTS 'not_now'"))
            conn.commit()
        except Exception:
            conn.rollback()  # not Postgres, or value already present
        try:
            conn.execute(text("ALTER TABLE call_attempts ALTER COLUMN finished_at DROP NOT NULL"))
            conn.commit()
        except Exception:
            conn.rollback()  # not Postgres, or already nullable

        # Backfill: any campaign/user created before multi-tenancy existed
        # gets grouped into a single "Legacy" organization so existing data
        # (and the bootstrap admin) keeps working under the new model.
        try:
            legacy = conn.execute(
                text("SELECT id FROM organizations WHERE name = 'Legacy'")
            ).fetchone()
            if legacy is None:
                conn.execute(
                    text("INSERT INTO organizations (name, created_at) VALUES ('Legacy', now())")
                )
                conn.commit()
                legacy = conn.execute(
                    text("SELECT id FROM organizations WHERE name = 'Legacy'")
                ).fetchone()
            legacy_id = legacy[0]
            conn.execute(
                text("UPDATE campaigns SET organization_id = :oid WHERE organization_id IS NULL"),
                {"oid": legacy_id},
            )
            conn.execute(
                text("UPDATE users SET organization_id = :oid WHERE organization_id IS NULL"),
                {"oid": legacy_id},
            )
            conn.commit()
        except Exception:
            conn.rollback()  # organizations table not present yet (fresh DB, nothing to backfill)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _scheduler_task, _telephony_watchdog, _db_keepalive_task
    if not getattr(_app.state, "disable_scheduler", False):
        _migrate_db()
        if _scheduler_task is None or _scheduler_task.done():
            _scheduler_task = asyncio.create_task(_scheduler_loop())
        if _telephony_watchdog is None or _telephony_watchdog.done():
            _telephony_watchdog = start_watchdog(get_telephony_store())
        if _db_keepalive_task is None or _db_keepalive_task.done():
            _db_keepalive_task = asyncio.create_task(_db_keepalive_loop())
    yield
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
    if _telephony_watchdog:
        _telephony_watchdog.cancel()
        _telephony_watchdog = None
    if _db_keepalive_task:
        _db_keepalive_task.cancel()
        _db_keepalive_task = None


app = FastAPI(title="VoiceSurvey AI Campaign Builder", version="0.1.0", lifespan=lifespan)
app.include_router(analytics_router)
app.include_router(analytics_global_router)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(operator_router)
app.include_router(settings_router)
app.include_router(settings_dnc_router)
app.include_router(settings_audit_router)
app.include_router(telephony_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def _track_activity(request: Request, call_next):
    global _last_activity_at
    _last_activity_at = datetime.now(timezone.utc)
    return await call_next(request)


SCHEDULER_TICK_SECONDS = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_execution(db: Session, campaign_id: int) -> CampaignExecution:
    execution = (
        db.query(CampaignExecution)
        .filter(CampaignExecution.campaign_id == campaign_id)
        .first()
    )
    if execution:
        return execution

    execution = CampaignExecution(campaign_id=campaign_id, state="idle")
    db.add(execution)
    db.flush()
    return execution


def _get_or_create_policy(db: Session, campaign_id: int) -> CallingPolicy:
    policy = (
        db.query(CallingPolicy)
        .filter(CallingPolicy.campaign_id == campaign_id)
        .first()
    )
    if policy:
        return policy

    policy = CallingPolicy(campaign_id=campaign_id)
    db.add(policy)
    db.flush()
    return policy


def _in_calling_window(campaign_timezone: str, policy: CallingPolicy, now_utc: datetime) -> bool:
    try:
        local_time = now_utc.astimezone(ZoneInfo(campaign_timezone))
    except ZoneInfoNotFoundError:
        local_time = now_utc

    hour = local_time.hour
    return policy.window_start_hour <= hour < policy.window_end_hour


def _next_attempt_eligible(
    db: Session,
    campaign_id: int,
    participant: Participant,
    policy: CallingPolicy,
    now_utc: datetime,
) -> tuple[bool, int]:
    attempts = (
        db.query(CallAttempt)
        .filter(
            CallAttempt.campaign_id == campaign_id,
            CallAttempt.participant_id == participant.id,
        )
        .order_by(CallAttempt.id.asc())
        .all()
    )
    attempt_number = len(attempts) + 1
    if len(attempts) >= policy.max_attempts:
        return False, attempt_number

    if not attempts:
        return True, attempt_number

    last_attempt = attempts[-1]
    if last_attempt.finished_at is None:
        # Still ringing/in-call (async dial flow) — not eligible for a new attempt yet.
        return False, attempt_number

    # DB DateTime columns aren't timezone-aware, so values read back from
    # Postgres are naive even though we always write UTC into them — normalize
    # before comparing against the aware `now_utc`, or this raises TypeError.
    finished_at = last_attempt.finished_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)

    retry_ready_at = finished_at + timedelta(minutes=policy.retry_delay_minutes)
    if last_attempt.outcome == "not_now":
        # The caller asked to be called back later — honor the short retry
        # delay only. The long cooldown exists to avoid re-bothering someone
        # who was already successfully reached, which doesn't apply here.
        ready_at = retry_ready_at
    else:
        cooldown_ready_at = finished_at + timedelta(hours=policy.cooldown_hours)
        ready_at = max(retry_ready_at, cooldown_ready_at)
    return now_utc >= ready_at, attempt_number


def _simulate_call_outcome(participant: Participant) -> tuple[str, str | None]:
    if participant.phone_number.endswith("9"):
        return "failed", "simulated temporary carrier failure"
    return "success", None


async def _dial_participant(db: Session, campaign: Campaign, participant: Participant, attempt_number: int) -> None:
    """Place a real outbound call for one participant and record a 'pending'
    CallAttempt — the actual outcome is filled in later by the Twilio status
    webhook (see webhook_status), since a real call takes time to ring/answer,
    unlike the old instant-simulated outcome."""
    now_utc = _utcnow()
    attempt = CallAttempt(
        campaign_id=campaign.id,
        participant_id=participant.id,
        attempt_number=attempt_number,
        outcome="pending",
        started_at=now_utc,
        finished_at=None,
    )
    db.add(attempt)
    db.flush()

    try:
        ctx, _result = await _create_and_start_voice_session(
            campaign, participant.phone_number, participant.locale, db
        )
        await get_gateway().initiate_call(
            to_number=participant.phone_number,
            campaign_id=campaign.id,
            session_id=ctx.session_id,
            store=get_telephony_store(),
        )
        attempt.session_id = ctx.session_id
        participant.status = "contacted"
        meta = participant.meta or {}
        meta["last_call_attempt_at"] = now_utc.isoformat()
        meta["attempt_number"] = attempt_number
        participant.meta = meta
    except Exception as exc:
        attempt.outcome = "failed"
        attempt.finished_at = _utcnow()
        attempt.note = f"dial error: {exc}"
        participant.status = "failed"
        meta = participant.meta or {}
        meta["last_call_attempt_at"] = now_utc.isoformat()
        meta["last_call_outcome"] = "failed"
        meta["last_call_note"] = attempt.note
        meta["attempt_number"] = attempt_number
        participant.meta = meta


async def _process_scheduler_tick(db: Session, execution: CampaignExecution):
    campaign = db.get(Campaign, execution.campaign_id)
    if not campaign:
        return

    policy = _get_or_create_policy(db, campaign.id)
    now_utc = _utcnow()

    if not policy.enabled:
        execution.last_tick_at = now_utc
        return

    if not _in_calling_window(campaign.timezone, policy, now_utc):
        execution.last_tick_at = now_utc
        return

    started_last_minute = (
        db.query(func.count(CallAttempt.id))
        .filter(
            CallAttempt.campaign_id == campaign.id,
            CallAttempt.started_at >= now_utc - timedelta(minutes=1),
        )
        .scalar()
        or 0
    )
    remaining_budget = max(policy.max_calls_per_minute - started_last_minute, 0)
    if remaining_budget <= 0:
        execution.last_tick_at = now_utc
        return

    candidates = (
        db.query(Participant)
        .filter(
            Participant.campaign_id == campaign.id,
            Participant.status.in_(["pending", "failed"]),
            Participant.opt_in.is_(True),
        )
        .order_by(Participant.id.asc())
        .all()
    )

    for participant in candidates:
        if remaining_budget <= 0:
            break

        eligible, attempt_number = _next_attempt_eligible(
            db,
            campaign.id,
            participant,
            policy,
            now_utc,
        )
        if not eligible:
            continue

        if is_blocked(db, participant.phone_number):
            continue

        await _dial_participant(db, campaign, participant, attempt_number)
        remaining_budget -= 1

    execution.last_tick_at = now_utc


async def _scheduler_loop():
    while True:
        db = SessionLocal()
        try:
            running = (
                db.query(CampaignExecution)
                .filter(CampaignExecution.state == "running")
                .all()
            )
            for execution in running:
                try:
                    await _process_scheduler_tick(db, execution)
                    db.commit()
                except Exception:
                    # One campaign's tick failing must not block every other
                    # running campaign's tick in the same pass.
                    logger.exception(
                        "Scheduler tick failed for campaign %s", execution.campaign_id
                    )
                    db.rollback()
        except Exception:
            logger.exception("Scheduler loop failed")
            db.rollback()
        finally:
            db.close()

        await asyncio.sleep(SCHEDULER_TICK_SECONDS)


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/voice")
def voice_simulator():
    return FileResponse("app/static/voice.html")


@app.get("/gallery")
def gallery_page():
    return FileResponse("app/static/gallery.html")


@app.get("/demo-call")
def demo_call_page():
    return FileResponse("app/static/demo-call.html")


@app.get("/operator")
def operator_page():
    return FileResponse("app/static/operator.html")


# ── WebSocket relay for browser demo calls ────────────────────────────────────
# Maps session_id → set of connected WebSocket clients (caller + operator).
# Each message is broadcast to all OTHER participants in the same room.

_ws_rooms: dict[str, set[WebSocket]] = defaultdict(set)


@app.websocket("/ws/call/{session_id}")
async def call_ws(websocket: WebSocket, session_id: str, role: str = "caller"):
    """Real-time relay between the caller page and the operator console.

    role: "caller" or "operator" — carried in each relayed message so the
    receiving end can style it correctly.
    """
    await websocket.accept()
    room = _ws_rooms[session_id]
    room.add(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            data["sender"] = role
            # Log text messages to ctx.history ONLY during an active operator takeover,
            # so the agent knows what was discussed. During normal survey turns the caller
            # also sends via WebSocket for operator visibility, but those messages are
            # already logged by the /turn endpoint — logging them here too would create
            # a duplicate caller_input that misleads the prior-profanity check.
            msg_text = data.get("text", "")
            msg_type = data.get("type")  # control frames: "hangup", "return_to_agent"
            if msg_text and not msg_type:
                session = _voice_sessions.get(session_id)
                if session:
                    snap = get_escalation_queue().get(session_id)
                    in_takeover = snap is not None and snap.operator_id is not None and snap.returned_at is None
                    if in_takeover:
                        event = "operator_message" if role == "operator" else "caller_input"
                        session["ctx"].log(event, text=msg_text, during_takeover=True)
            dead = set()
            for peer in list(room):
                if peer is websocket:
                    continue
                try:
                    await peer.send_json(data)
                except Exception:
                    dead.add(peer)
            room -= dead
    except (WebSocketDisconnect, Exception):
        room.discard(websocket)
        # If the caller disconnected, clean up the session so the operator queue
        # doesn't show stale entries. More reliable than sendBeacon which can be
        # cancelled mid-unload by the browser.
        if role == "caller":
            _voice_sessions.pop(session_id, None)
            get_escalation_queue().remove(session_id)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "campaign-builder"}


def get_owned_campaign(
    campaign_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Campaign:
    """Fetch a campaign and verify it belongs to the current user's organization.

    Returns 404 (not 403) for campaigns owned by another organization, so the
    endpoint doesn't leak which campaign IDs exist for other companies.
    """
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@app.post("/api/campaigns", response_model=CampaignOut)
def create_campaign(
    payload: CampaignCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = Campaign(**payload.model_dump(), organization_id=user.organization_id)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@app.get("/api/campaigns", response_model=list[CampaignOut])
def list_campaigns(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Campaign)
        .filter(Campaign.organization_id == user.organization_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )


@app.get("/api/campaigns/summary", response_model=list[CampaignSummary])
def list_campaign_summaries(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
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
        .filter(Campaign.organization_id == user.organization_id)
        .outerjoin(Question, Question.campaign_id == Campaign.id)
        .outerjoin(Participant, Participant.campaign_id == Campaign.id)
        .group_by(Campaign.id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return [CampaignSummary.model_validate(dict(row._mapping)) for row in rows]


@app.get("/api/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign: Campaign = Depends(get_owned_campaign)):
    return campaign


@app.get("/api/campaigns/{campaign_id}/full")
def get_campaign_full(
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    """Everything the campaign builder page needs, in one round-trip.

    The builder used to fire ~7 sequential requests (campaign, questions,
    rules, participants, execution, policy, attempts) — each one paying a
    full network round-trip. Against a remote DB (vs. the near-zero latency
    of localhost) that serial chain alone added several seconds per page
    open, so it's collapsed into a single response here.
    """
    campaign_id = campaign.id

    execution = _get_or_create_execution(db, campaign_id)
    policy = _get_or_create_policy(db, campaign_id)
    db.commit()
    db.refresh(execution)
    db.refresh(policy)

    questions = (
        db.query(Question)
        .filter(Question.campaign_id == campaign_id)
        .order_by(Question.order_index.asc())
        .all()
    )
    rules = (
        db.query(BranchRule)
        .filter(BranchRule.campaign_id == campaign_id)
        .order_by(BranchRule.priority.asc(), BranchRule.id.asc())
        .all()
    )
    participants = (
        db.query(Participant)
        .filter(Participant.campaign_id == campaign_id)
        .order_by(Participant.id.desc())
        .all()
    )
    attempt_rows = (
        db.query(CallAttempt, Participant.phone_number)
        .join(Participant, Participant.id == CallAttempt.participant_id)
        .filter(CallAttempt.campaign_id == campaign_id)
        .order_by(CallAttempt.id.desc())
        .limit(30)
        .all()
    )

    return {
        "campaign": CampaignOut.model_validate(campaign),
        "questions": [QuestionOut.model_validate(q) for q in questions],
        "rules": [RuleOut.model_validate(r) for r in rules],
        "participants": [ParticipantOut.model_validate(p) for p in participants],
        "execution": CampaignExecutionOut(
            campaign_id=campaign_id,
            state=execution.state,
            started_at=execution.started_at,
            paused_at=execution.paused_at,
            stopped_at=execution.stopped_at,
            last_tick_at=execution.last_tick_at,
        ),
        "policy": CallingPolicyOut(
            campaign_id=campaign_id,
            window_start_hour=policy.window_start_hour,
            window_end_hour=policy.window_end_hour,
            max_attempts=policy.max_attempts,
            retry_delay_minutes=policy.retry_delay_minutes,
            cooldown_hours=policy.cooldown_hours,
            max_calls_per_minute=policy.max_calls_per_minute,
            enabled=policy.enabled,
        ),
        "attempts": [
            CallAttemptOut(
                id=attempt.id,
                participant_id=attempt.participant_id,
                participant_phone=phone,
                attempt_number=attempt.attempt_number,
                outcome=attempt.outcome,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                note=attempt.note,
            )
            for attempt, phone in attempt_rows
        ],
    }


@app.put("/api/campaigns/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    payload: CampaignUpdate,
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(campaign, key, value)
    campaign.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(campaign)
    return campaign


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    campaign_id = campaign.id

    # Manually cascade-delete dependents in FK-safe order — none of these
    # relationships have ON DELETE CASCADE configured at the DB level, so a
    # bare db.delete(campaign) fails with a ForeignKeyViolation as soon as any
    # analytics/call data exists for the campaign.
    answer_ids = [
        row[0] for row in db.query(Answer.id).filter(Answer.campaign_id == campaign_id).all()
    ]
    if answer_ids:
        db.query(AnswerLabel).filter(AnswerLabel.answer_id.in_(answer_ids)).delete(synchronize_session=False)
    db.query(AnswerFactCheck).filter(AnswerFactCheck.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(FreeTextAnalysis).filter(FreeTextAnalysis.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(CrossSurveyMatch).filter(
        (CrossSurveyMatch.source_campaign_id == campaign_id)
        | (CrossSurveyMatch.target_campaign_id == campaign_id)
    ).delete(synchronize_session=False)
    db.query(EntityMention).filter(EntityMention.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(ConversationTurn).filter(ConversationTurn.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(Answer).filter(Answer.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(FreeTextLabel).filter(FreeTextLabel.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(DemographicWeight).filter(DemographicWeight.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(CallLog).filter(CallLog.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(CallingPolicy).filter(CallingPolicy.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(Participant).filter(Participant.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(BranchRule).filter(BranchRule.campaign_id == campaign_id).delete(synchronize_session=False)
    db.query(Question).filter(Question.campaign_id == campaign_id).delete(synchronize_session=False)

    db.delete(campaign)
    db.commit()
    return {"deleted": True}


@app.post("/api/campaigns/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate_campaign(
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    clone = Campaign(
        name=f"{campaign.name} (Copy)",
        language=campaign.language,
        timezone=campaign.timezone,
        consent_text=campaign.consent_text,
        status="draft",
        organization_id=campaign.organization_id,
    )
    db.add(clone)
    db.flush()

    sorted_questions = sorted(campaign.questions, key=lambda q: q.order_index)
    new_questions = [
        Question(
            campaign_id=clone.id,
            order_index=question.order_index,
            key=f"{question.key}_copy_{clone.id}",
            prompt=question.prompt,
            question_type=question.question_type,
            required=question.required,
            config=question.config,
        )
        for question in sorted_questions
    ]
    db.add_all(new_questions)
    db.flush()
    id_map = {
        old_question.id: new_question.id
        for old_question, new_question in zip(sorted_questions, new_questions)
    }

    new_rules = [
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
        for rule in campaign.rules
    ]
    db.add_all(new_rules)

    db.commit()
    db.refresh(clone)
    return clone


@app.post("/api/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    campaign_id = campaign.id
    execution = _get_or_create_execution(db, campaign_id)
    if execution.state == "stopped":
        raise HTTPException(status_code=409, detail="Campaign is stopped. Use start to run it again")

    campaign.status = "paused"
    execution.state = "paused"
    execution.paused_at = _utcnow()
    db.commit()
    db.refresh(campaign)
    return campaign


@app.post("/api/campaigns/{campaign_id}/resume", response_model=CampaignOut)
def resume_campaign(
    campaign: Campaign = Depends(get_owned_campaign),
    db: Session = Depends(get_db),
):
    campaign_id = campaign.id
    execution = _get_or_create_execution(db, campaign_id)
    if execution.state == "stopped":
        raise HTTPException(status_code=409, detail="Campaign is stopped. Use start to run it again")

    campaign.status = "active"
    execution.state = "running"
    if execution.started_at is None:
        execution.started_at = _utcnow()
    db.commit()
    db.refresh(campaign)
    return campaign


@app.get("/api/campaigns/{campaign_id}/execution", response_model=CampaignExecutionOut)
def get_campaign_execution(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    execution = _get_or_create_execution(db, campaign_id)
    db.commit()
    db.refresh(execution)
    return CampaignExecutionOut(
        campaign_id=campaign_id,
        state=execution.state,
        started_at=execution.started_at,
        paused_at=execution.paused_at,
        stopped_at=execution.stopped_at,
        last_tick_at=execution.last_tick_at,
    )


@app.post("/api/campaigns/{campaign_id}/start", response_model=CampaignExecutionOut)
def start_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    execution = _get_or_create_execution(db, campaign_id)
    if execution.state == "running":
        raise HTTPException(status_code=409, detail="Campaign is already running")

    now_utc = _utcnow()
    execution.state = "running"
    execution.started_at = now_utc
    execution.paused_at = None
    execution.stopped_at = None
    campaign.status = "active"

    db.commit()
    db.refresh(execution)
    return CampaignExecutionOut(
        campaign_id=campaign_id,
        state=execution.state,
        started_at=execution.started_at,
        paused_at=execution.paused_at,
        stopped_at=execution.stopped_at,
        last_tick_at=execution.last_tick_at,
    )


@app.post("/api/campaigns/{campaign_id}/stop", response_model=CampaignExecutionOut)
def stop_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    execution = _get_or_create_execution(db, campaign_id)
    if execution.state == "stopped":
        raise HTTPException(status_code=409, detail="Campaign is already stopped")

    now_utc = _utcnow()
    execution.state = "stopped"
    execution.stopped_at = now_utc
    campaign.status = "paused"

    db.commit()
    db.refresh(execution)
    return CampaignExecutionOut(
        campaign_id=campaign_id,
        state=execution.state,
        started_at=execution.started_at,
        paused_at=execution.paused_at,
        stopped_at=execution.stopped_at,
        last_tick_at=execution.last_tick_at,
    )


@app.get("/api/campaigns/{campaign_id}/policy", response_model=CallingPolicyOut)
def get_calling_policy(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    policy = _get_or_create_policy(db, campaign_id)
    db.commit()
    db.refresh(policy)
    return CallingPolicyOut(
        campaign_id=campaign_id,
        window_start_hour=policy.window_start_hour,
        window_end_hour=policy.window_end_hour,
        max_attempts=policy.max_attempts,
        retry_delay_minutes=policy.retry_delay_minutes,
        cooldown_hours=policy.cooldown_hours,
        max_calls_per_minute=policy.max_calls_per_minute,
        enabled=policy.enabled,
    )


@app.put("/api/campaigns/{campaign_id}/policy", response_model=CallingPolicyOut)
def update_calling_policy(
    campaign_id: int,
    payload: CallingPolicyUpdate,
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    policy = _get_or_create_policy(db, campaign_id)
    for key, value in payload.model_dump().items():
        setattr(policy, key, value)

    db.commit()
    db.refresh(policy)
    return CallingPolicyOut(
        campaign_id=campaign_id,
        window_start_hour=policy.window_start_hour,
        window_end_hour=policy.window_end_hour,
        max_attempts=policy.max_attempts,
        retry_delay_minutes=policy.retry_delay_minutes,
        cooldown_hours=policy.cooldown_hours,
        max_calls_per_minute=policy.max_calls_per_minute,
        enabled=policy.enabled,
    )


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


@app.get("/api/campaigns/{campaign_id}/attempts", response_model=list[CallAttemptOut])
def list_call_attempts(campaign_id: int, limit: int = 30, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    safe_limit = max(1, min(limit, 200))
    rows = (
        db.query(CallAttempt, Participant.phone_number)
        .join(Participant, Participant.id == CallAttempt.participant_id)
        .filter(CallAttempt.campaign_id == campaign_id)
        .order_by(CallAttempt.id.desc())
        .limit(safe_limit)
        .all()
    )

    return [
        CallAttemptOut(
            id=attempt.id,
            participant_id=attempt.participant_id,
            participant_phone=phone,
            attempt_number=attempt.attempt_number,
            outcome=attempt.outcome,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            note=attempt.note,
        )
        for attempt, phone in rows
    ]


# ===========================================================================
# SAA-50: Voice AI Pipeline endpoints
# ===========================================================================

# In-memory session store (keyed by session_id).
_voice_sessions: dict[str, dict] = {}

# SAA-79/81: global mirroring settings (mutated in place by the settings endpoint)
_mirroring_settings = MirroringSettings(
    smoothing_alpha=0.60,    # high alpha: EMA reacts within 1-2 turns (was 0.30)
    calibration_turns=1,     # lock baseline after first turn (was 2)
    max_rate_delta=0.35,     # ±35% range → 0.65x–1.35x (was ±20%)
)
_agent_service = AgentAIService()   # uses ANTHROPIC_API_KEY env var; falls back to rules if absent
_pipeline = VoicePipeline(mirroring_settings=_mirroring_settings, agent_service=_agent_service)

# Wire session store into dashboard router so live-calls endpoint can read it.
set_live_sessions_store(_voice_sessions)
# Wire session store into telephony router so TwiML handlers can speak the
# pipeline's real greeting/question instead of a generic hardcoded line.
set_voice_sessions_store(_voice_sessions)


class VoiceSessionStartRequest(BaseModel):
    participant_phone: str
    locale: str | None = None


class VoiceTurnRequest(BaseModel):
    transcript: str          # text from caller (or forwarded from STT adapter)
    audio_duration_ms: float | None = 0.0       # None-safe: JS may send null on click events
    mic_hesitation_count: int | None = 0        # pauses detected by Web Audio API silence analysis


# SAA-80/81: Pydantic schema for mirroring settings
class MirroringSettingsRequest(BaseModel):
    enabled: bool = True
    max_rate_delta: float = 0.35
    max_pitch_semitones: float = 2.0
    kill_switch_rapport_threshold: float = 0.50
    smoothing_alpha: float = 0.60
    calibration_turns: int = 1
    baseline_drift_alpha: float = 0.04
    rapport_rate_weight: bool = True


_HESITATION_WORDS = frozenset(
    {"um", "uh", "er", "hmm", "ah", "ehm", "like", "well", "אממ", "אה", "אהמ"}
)


def _estimate_mock_confidence(text: str) -> float:
    """Derive a realistic STT confidence from the transcript text.

    Clean, fluent answers → high confidence (~0.90–0.95).
    Hesitation markers (um, uh, er …) → lower confidence (~0.55–0.75).
    Very short / empty responses → floor at 0.55.
    """
    words = text.lower().split()
    if not words:
        return 0.55
    hesitations = sum(1 for w in words if w.strip(".,!?;:") in _HESITATION_WORDS)
    hesitation_rate = hesitations / len(words)
    confidence = 0.93 - hesitation_rate * 0.45
    if len(words) == 1:
        confidence = min(confidence, 0.88)   # single-word answers are less certain
    return round(max(0.48, min(0.97, confidence)), 3)


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


async def _create_and_start_voice_session(
    campaign: Campaign,
    participant_phone: str,
    locale: str | None,
    db: Session,
):
    """Shared by the manual voice-session API and the campaign auto-dial
    scheduler: builds the pipeline session, registers it, and logs the call."""
    question_contexts = _load_question_contexts(campaign.id, db)
    if not question_contexts:
        raise ValueError("Campaign has no questions")

    # Load branch rules and convert to plain objects so they survive session close
    _branch_rule_rows = (
        db.query(BranchRule)
        .filter(BranchRule.campaign_id == campaign.id)
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
        campaign_id=campaign.id,
        participant_phone=participant_phone,
        questions=question_contexts,
        branch_rules=branch_rules,
        language=campaign.language,
        locale=locale,
        campaign_name=campaign.name,
        campaign_description=campaign.description or "",
    )

    result = await _pipeline.start_session(ctx)

    _voice_sessions[ctx.session_id] = {
        "ctx": ctx,
        "campaign_id": campaign.id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tts_metrics": [],
        "stt_metrics": [],
        "last_response_text": result.response_text,
    }

    # SAA-101: persist call log for dashboard metrics
    call_log = CallLog(
        campaign_id=campaign.id,
        session_id=ctx.session_id,
        status="active",
        started_at=datetime.now(timezone.utc),
    )
    db.add(call_log)
    db.commit()

    return ctx, result


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

    try:
        ctx, result = await _create_and_start_voice_session(campaign, payload.participant_phone, payload.locale, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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

    # On [resume] (operator handing back to agent): reset the FSM to a clean
    # asking state so accumulated retry counters from before the escalation
    # don't immediately re-trigger a fallback escalation.
    # If the session has already escalated 3+ times, end the call instead of
    # resuming — repeated escalations with no resolution signal the call cannot
    # be completed productively.
    if payload.transcript.strip() == "[resume]":
        from .voice.dialogue.fsm import DialogueState
        prior_escalations = sum(1 for e in ctx.history if e.get("event") == "escalate")
        if prior_escalations >= 3:
            he = getattr(ctx, "_language", "en").startswith("he")
            end_text = (
                "נראה שהשיחה לא יכולה להמשיך. תודה על זמנך. שלום."
                if he else
                "It seems we're unable to continue this call. Thank you for your time. Goodbye."
            )
            ctx.state = DialogueState.DONE
            ctx.log("session_end", reason="repeated_escalations")
            return {
                "session_id": session_id,
                "response_text": end_text,
                "dialogue_action": "speak_closing",
                "current_state": "done",
                "current_question_key": None,
                "session_complete": True,
                "stt_metrics": None,
                "tts_metrics": None,
                "mirroring": None,
                "escalation_snapshot": None,
            }
        ctx.state = DialogueState.ASKING
        ctx.retry_count = 0
        # Return the current question directly — skip the full pipeline so
        # the [resume] text does not trigger a fresh escalation evaluation.
        current_q = ctx.current_question
        resume_text = current_q.prompt if current_q else (
            "נמשיך בסקר." if getattr(ctx, "_language", "en").startswith("he") else "Let's continue the survey."
        )
        return {
            "session_id": session_id,
            "response_text": resume_text,
            "dialogue_action": "speak_question",
            "current_state": ctx.state,
            "current_question_key": current_q.question_key if current_q else None,
            "session_complete": False,
            "stt_metrics": None,
            "tts_metrics": None,
            "mirroring": None,
            "escalation_snapshot": None,
        }

    # Wrap the provided transcript as an async generator of bytes so the
    # STT adapter's interface is satisfied (mock adapter ignores audio bytes
    # and uses preset responses; real adapters would receive actual audio).
    async def _text_as_audio_chunks():
        yield payload.transcript.encode()

    # Override mock STT to return the submitted transcript directly.
    # Confidence is estimated from the text so rapport actually varies:
    # hesitation markers lower it, short/unclear answers drop it further.
    # If the frontend measured real mic duration, derive ms_per_word from it so
    # WPM reflects the caller's actual speaking pace.
    from .voice.stt.adapter import MockSTTAdapter
    _words = payload.transcript.split()
    _word_count = max(1, len(_words))
    _dur = payload.audio_duration_ms or 0.0
    _ms_per_word = _dur / _word_count if _dur > 0 else None

    # STT mock returns the clean transcript — NLU and stored answers stay clean.
    # Mic-detected hesitations are passed separately to process_turn for feature extraction only.
    _pipeline._stt = MockSTTAdapter(
        responses=[payload.transcript],
        confidence=_estimate_mock_confidence(payload.transcript),
        ms_per_word=_ms_per_word,
    )

    _hesit_n = min(payload.mic_hesitation_count or 0, 6)
    result = await _pipeline.process_turn(ctx, _text_as_audio_chunks(), hesitation_count=_hesit_n)

    session["tts_metrics"].append(result.tts_metrics)
    session["stt_metrics"].append(result.stt_metrics)

    if result.session_complete:
        session["completed_at"] = datetime.now(timezone.utc).isoformat()
        session["answers"] = dict(ctx.answers)

    # SAA-101: update CallLog with status, answers, rapport, voice metrics, history
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log:
        call_log.turns_count = len(ctx.history)
        call_log.answers = dict(ctx.answers)

        # Persist each answer as a real `Answer` row too — not just the JSON
        # blob on CallLog. NER/sentiment/fact-check analysis and cross-survey
        # matching (run_cross_survey.py) all read from the `answers` table,
        # so without this they silently never see any data from real calls.
        questions_by_key = {q.question_key: q for q in ctx.questions}
        for key, value in ctx.answers.items():
            q = questions_by_key.get(key)
            if not q:
                continue
            existing_answer = (
                db.query(Answer)
                .filter(
                    Answer.session_id == session_id,
                    Answer.campaign_id == campaign_id,
                    Answer.question_key == key,
                )
                .first()
            )
            if existing_answer:
                existing_answer.raw_text = value
                existing_answer.normalized_value = value
            else:
                db.add(Answer(
                    session_id=session_id,
                    campaign_id=campaign_id,
                    question_id=q.question_id,
                    question_key=key,
                    raw_text=value,
                    normalized_value=value,
                    answer_type=q.question_type,
                ))

        # Rapport = avg STT confidence from caller_input events
        confidences = [
            e["confidence"] for e in ctx.history
            if e.get("event") == "caller_input" and isinstance(e.get("confidence"), float) and e["confidence"] > 0
        ]
        call_log.rapport_score = round(sum(confidences) / len(confidences), 2) if confidences else None

        # Voice analytics: persist the calibration state (speaking rate, pitch,
        # hesitation, energy) so it survives server restarts.
        cal = ctx.mirroring_calibration
        call_log.voice_metrics = {
            "turns_observed":  cal.turns_observed,
            "is_calibrated":   cal.is_calibrated,
            "calibration_turns": cal.calibration_turns,
            "smoothed":  cal.smoothed.to_dict() if cal.smoothed else None,
            "baseline":  cal.baseline.to_dict() if cal.baseline else None,
        }

        # Full conversation history for audit / dashboard replay
        call_log.history = list(ctx.history)

        action = result.dialogue_action.value
        # Mark escalated in DB immediately — session stays open for operator takeover
        if "escalat" in action:
            call_log.status = "escalated"

        # True if this call ever triggered escalation (checked via in-memory history)
        call_was_escalated = any(e.get("event") == "escalate" for e in ctx.history)

        if result.session_complete:
            call_log.ended_at = datetime.now(timezone.utc)
            # Once a call required intervention, keep "escalated" as the final status
            # so the dashboard cumulative count never drops.
            if not call_was_escalated and "escalat" not in action:
                if "closing" in action or "end" in action:
                    # ctx.state is always DialogueState.DONE at this point regardless
                    # of why the call closed — the actual reason (e.g. the caller
                    # asked to be called back later) is only visible in the intent
                    # logged on the last "turn" history entry, not in ctx.state.
                    last_intent = next(
                        (e.get("intent") for e in reversed(ctx.history) if e.get("event") == "turn"),
                        None,
                    )
                    call_log.status = "not_now" if last_intent == IntentType.NOT_NOW else "completed"
                else:
                    call_log.status = "completed"
            elif call_was_escalated:
                call_log.status = "escalated"
            # Remove from operator queue if the agent closed the call naturally
            # (escalated sessions stay until the operator explicitly handles them)
            if not call_was_escalated:
                get_escalation_queue().remove(session_id)
        db.commit()

    # SAA-78/82: include mirroring decision + monitoring flags in turn response
    mirroring_resp = None
    if result.mirroring_decision is not None:
        md = result.mirroring_decision
        mirroring_resp = {
            **md.to_dict(),
            "monitoring_flags": _pipeline._mirroring_policy.monitoring_flags(
                ctx.mirroring_calibration,
                _pipeline._compute_rapport(ctx),
            ),
            "calibration": ctx.mirroring_calibration.to_dict(),
        }
        session["last_mirroring"] = mirroring_resp

    session["last_response_text"] = result.response_text
    esc = result.escalation_snapshot
    return {
        "session_id": session_id,
        "response_text": result.response_text,
        "dialogue_action": result.dialogue_action,
        "current_state": result.current_state,
        "current_question_key": result.current_question_key,
        "session_complete": result.session_complete,
        "stt_metrics": result.stt_metrics,
        "tts_metrics": result.tts_metrics,
        "mirroring": mirroring_resp,
        "escalation_snapshot": esc.to_dict() if esc else None,
    }


@app.get("/api/campaigns/{campaign_id}/voice/sessions/{session_id}")
def get_voice_session(campaign_id: int, session_id: str):
    """SAA-50: Get current state of a voice session."""
    session = _voice_sessions.get(session_id)
    if not session or session["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Voice session not found")

    ctx = session["ctx"]
    rapport = _pipeline._compute_rapport(ctx)
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
        "demo_running": session.get("demo_running", False),
        "tts_metrics_summary": session["tts_metrics"],
        "stt_metrics_summary": session["stt_metrics"],
        "history": ctx.history,
        "mirroring": {
            "calibration": ctx.mirroring_calibration.to_dict(),
            "monitoring_flags": _pipeline._mirroring_policy.monitoring_flags(
                ctx.mirroring_calibration, rapport
            ),
            "rapport": rapport,
            **(session.get("last_mirroring") or {}),
        },
    }


@app.delete("/api/campaigns/{campaign_id}/voice/sessions/{session_id}")
def end_voice_session(campaign_id: int, session_id: str, db: Session = Depends(get_db)):
    """SAA-50: Terminate and discard a voice session."""
    session = _voice_sessions.pop(session_id, None)
    if not session or session["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Voice session not found")
    get_escalation_queue().remove(session_id)
    # Mark any still-active CallLog as failed
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log and call_log.status == "active":
        call_log.status = "failed"
        call_log.ended_at = datetime.now(timezone.utc)
        db.commit()
    return {"ended": True, "session_id": session_id}


@app.post("/api/voice/sessions/{session_id}/disconnect")
def caller_disconnected(session_id: str, db: Session = Depends(get_db)):
    """Called via sendBeacon when the caller's browser page is closed or refreshed.

    Removes the session from the escalation queue so the operator queue
    stays clean. The in-memory voice session is also removed so there is
    no stale state if the caller reconnects with a new session.
    """
    _voice_sessions.pop(session_id, None)
    get_escalation_queue().remove(session_id)
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log and call_log.status in ("active", "escalated"):
        call_log.status = "failed"
        call_log.ended_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@app.get("/dashboard")
def dashboard():
    return FileResponse("app/static/dashboard.html")


# ===========================================================================
# SAA-79/80/81: Voice Mirroring Settings
# ===========================================================================

@app.get("/api/mirroring/settings")
def get_mirroring_settings():
    """SAA-81: Return the current global voice mirroring settings."""
    s = _mirroring_settings
    return {
        "enabled": s.enabled,
        "max_rate_delta": s.max_rate_delta,
        "max_pitch_semitones": s.max_pitch_semitones,
        "kill_switch_rapport_threshold": s.kill_switch_rapport_threshold,
        "smoothing_alpha": s.smoothing_alpha,
        "calibration_turns": s.calibration_turns,
        "baseline_drift_alpha": s.baseline_drift_alpha,
        "rapport_rate_weight": s.rapport_rate_weight,
    }


@app.put("/api/mirroring/settings")
def update_mirroring_settings(payload: MirroringSettingsRequest):
    """SAA-81: Update global mirroring settings (applied to all new + running sessions)."""
    _mirroring_settings.enabled = payload.enabled
    _mirroring_settings.max_rate_delta = max(0.0, min(0.50, payload.max_rate_delta))
    _mirroring_settings.max_pitch_semitones = max(0.0, min(6.0, payload.max_pitch_semitones))
    _mirroring_settings.kill_switch_rapport_threshold = max(0.0, min(1.0, payload.kill_switch_rapport_threshold))
    _mirroring_settings.smoothing_alpha = max(0.05, min(0.95, payload.smoothing_alpha))
    _mirroring_settings.calibration_turns = max(1, min(10, payload.calibration_turns))
    _mirroring_settings.baseline_drift_alpha = max(0.0, min(0.20, payload.baseline_drift_alpha))
    _mirroring_settings.rapport_rate_weight = payload.rapport_rate_weight
    return get_mirroring_settings()


# ===========================================================================
# Flat session endpoints (architecture doc test-code compatible)
# ===========================================================================

# In-memory operator escalation queue ordered by insertion time.
# Each entry: {session_id, campaign_id, reason, rapport_score, transcript,
#              current_question_key, queued_at, participant_phone}
_operator_queue: list[dict] = []


class SessionEventRequest(BaseModel):
    type: str           # e.g. "INTENT"
    intent: str | None = None
    data: dict | None = None


class HandoverRequest(BaseModel):
    reason: str         # e.g. "LOW_CONFIDENCE", "OPERATOR_REQUEST"


_INTENT_TO_ACTION: dict[str, str] = {
    "REPEAT_QUESTION": "REPEAT",
    "REPHRASE_QUESTION": "REPHRASE",
    "NOT_NOW": "SCHEDULE_CALLBACK",
    "OPT_OUT": "END_CALL",
    "SKIP": "SKIP",
    "HELP": "REPHRASE",
    "CONFIRM_YES": "ACCEPT_ANSWER",
    "CONFIRM_NO": "REPEAT",
}


@app.get("/api/live-calls")
def live_calls_flat(campaign_id: int | None = None) -> dict:
    """Architecture-doc compatible live-calls endpoint.

    Returns {"calls": [...], "count": N} where each call includes
    current_question_index and total_questions as required by User Story 6.
    """
    calls = []
    for sid, s in _voice_sessions.items():
        if campaign_id and s.get("campaign_id") != campaign_id:
            continue
        if s.get("completed_at"):
            continue
        ctx = s.get("ctx")
        if ctx is None:
            continue

        total_q = len(ctx.questions)
        answered = len(ctx.answers)
        confidences = [
            e["confidence"] for e in ctx.history
            if e.get("event") == "turn" and "confidence" in e and e["confidence"] > 0
        ]
        rapport = round(sum(confidences) / len(confidences), 2) if confidences else None

        calls.append({
            "session_id": sid,
            "campaign_id": s.get("campaign_id"),
            "participant_phone": ctx.participant_phone,
            "current_state": str(ctx.state),
            "current_question_index": ctx.current_question_index,
            "current_question_key": (ctx.current_question.question_key if ctx.current_question else None),
            "total_questions": total_q,
            "questions_answered": answered,
            "progress_pct": round(answered / total_q * 100) if total_q else 0,
            "rapport_score": rapport,
            "started_at": s.get("started_at"),
        })

    return {"calls": calls, "count": len(calls)}


@app.post("/api/sessions/{session_id}/event")
async def session_event(
    session_id: str,
    payload: SessionEventRequest,
    db: Session = Depends(get_db),
):
    """Process a session intent event (User Story 7: repeat/rephrase/opt-out etc.).

    Dispatches the intent through the voice pipeline and returns next_action.
    """
    session = _voice_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Voice session not found")

    ctx = session["ctx"]

    if payload.type != "INTENT" or not payload.intent:
        raise HTTPException(status_code=400, detail="type must be 'INTENT' and intent must be set")

    intent_upper = payload.intent.upper()
    next_action = _INTENT_TO_ACTION.get(intent_upper)

    if not next_action:
        raise HTTPException(status_code=400, detail=f"Unknown intent: {payload.intent}")

    # For REPEAT / REPHRASE intents, drive the pipeline with a matching utterance
    # so the FSM state is updated correctly.
    intent_utterances: dict[str, str] = {
        "REPEAT_QUESTION": "please repeat that",
        "REPHRASE_QUESTION": "can you rephrase",
        "NOT_NOW": "not now",
        "OPT_OUT": "opt out",
        "SKIP": "skip",
        "HELP": "i need help",
        "CONFIRM_YES": "yes",
        "CONFIRM_NO": "no",
    }
    utterance = intent_utterances.get(intent_upper, payload.intent.lower())

    from .voice.stt.adapter import MockSTTAdapter
    _pipeline._stt = MockSTTAdapter(responses=[utterance], confidence=0.95)

    async def _chunks():
        yield utterance.encode()

    result = await _pipeline.process_turn(ctx, _chunks())
    session["stt_metrics"].append(result.stt_metrics)
    session["tts_metrics"].append(result.tts_metrics)

    if result.session_complete:
        session["completed_at"] = datetime.now(timezone.utc).isoformat()
        session["answers"] = dict(ctx.answers)

    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log:
        call_log.turns_count = len(ctx.history)
        if result.session_complete:
            call_log.ended_at = datetime.now(timezone.utc)
            call_log.status = "completed"
        db.commit()

    return {
        "session_id": session_id,
        "intent": payload.intent,
        "next_action": next_action,
        "response_text": result.response_text,
        "current_state": result.current_state,
        "session_complete": result.session_complete,
    }


@app.post("/api/sessions/{session_id}/handover")
def session_handover(session_id: str, payload: HandoverRequest, db: Session = Depends(get_db)):
    """Escalate a voice session to the human operator queue (User Story 10).

    Returns {"status": "QUEUED_FOR_OPERATOR"} and pushes full context into
    the priority queue sorted by rapport_score ascending (lowest confidence first).
    """
    session = _voice_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Voice session not found")

    ctx = session["ctx"]

    confidences = [
        e["confidence"] for e in ctx.history
        if e.get("event") == "turn" and "confidence" in e and e["confidence"] > 0
    ]
    rapport = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    transcript = [
        {"role": "agent" if e.get("event") == "speak" else "caller", "text": e.get("text", "")}
        for e in ctx.history
        if e.get("text")
    ]

    entry = {
        "session_id": session_id,
        "campaign_id": session.get("campaign_id"),
        "participant_phone": ctx.participant_phone,
        "reason": payload.reason,
        "rapport_score": rapport,
        "current_question_key": (ctx.current_question.question_key if ctx.current_question else None),
        "current_question_index": ctx.current_question_index,
        "total_questions": len(ctx.questions),
        "transcript": transcript,
        "answers_so_far": dict(ctx.answers),
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    # Insert sorted by rapport_score ascending (lowest confidence = highest priority)
    inserted = False
    for i, item in enumerate(_operator_queue):
        if rapport <= item["rapport_score"]:
            _operator_queue.insert(i, entry)
            inserted = True
            break
    if not inserted:
        _operator_queue.append(entry)

    # Update call log
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    if call_log:
        call_log.status = "escalated"
        call_log.ended_at = datetime.now(timezone.utc)
        call_log.rapport_score = rapport if rapport else call_log.rapport_score
        db.commit()

    # Mark session as escalated so live-calls board reflects it
    session["escalated"] = True
    session["completed_at"] = datetime.now(timezone.utc).isoformat()

    queue_position = next(
        (i + 1 for i, item in enumerate(_operator_queue) if item["session_id"] == session_id),
        len(_operator_queue),
    )

    return {
        "status": "QUEUED_FOR_OPERATOR",
        "session_id": session_id,
        "queue_position": queue_position,
        "rapport_score": rapport,
        "reason": payload.reason,
    }


@app.get("/api/operator-queue")
def get_operator_queue() -> dict:
    """Return the current Human-in-the-Loop escalation queue.

    Ordered by priority (lowest rapport_score first so operators handle
    the most uncertain calls first). Includes full transcript + context.
    """
    return {
        "queue": _operator_queue,
        "count": len(_operator_queue),
    }


_demo_run_tasks: dict[str, asyncio.Task] = {}


def _mock_answer_for_question(
    question_type: str,
    _config: dict,
    state: str,
    language: str = "en",
    hesitant: bool = False,
) -> str:
    """Generate a realistic mock answer. hesitant=True adds filler words."""
    import random
    hebrew = language.startswith("he")
    if "confirming" in state.lower():
        return "כן, זה נכון" if hebrew else "yes that is correct"

    if question_type == "rating":
        if hebrew:
            fluent   = ["אני נותן שמונה", "הייתי אומר תשע", "אני מדרג שבע", "בעיניי תשע מתוך עשר"]
            hesitant_ = ["אממ אני חושב שמונה בערך", "אה אולי שבע אני לא בטוח", "אממ כנראה תשע"]
        else:
            fluent   = ["I would rate it eight", "I would say nine out of ten", "I give it a seven", "definitely a nine"]
            hesitant_ = ["um well maybe like eight I think", "uh probably around seven", "hmm I would say nine I guess"]
    elif question_type == "mcq":
        if hebrew:
            fluent   = ["אני בוחר אפשרות א", "הייתי אומר ב", "התשובה שלי היא א"]
            hesitant_ = ["אממ אני חושב אפשרות א", "אה אולי ב אני לא בטוח"]
        else:
            fluent   = ["I would choose option A", "I think option B is right", "my answer is A"]
            hesitant_ = ["um well I think option A", "uh maybe B I am not sure"]
    else:  # free_text
        if hebrew:
            fluent   = ["השירות היה מצוין לחלוטין", "הייתי מרוצה מאוד מהחוויה", "הכל עבד בסדר גמור"]
            hesitant_ = ["אממ ובכן השירות היה בסדר אני חושב", "אה לא יודע אולי בסדר גמור"]
        else:
            fluent   = ["the service was excellent overall", "I was very satisfied with the experience", "everything worked out well"]
            hesitant_ = ["um well the service was okay I think", "uh I am not really sure maybe it was good"]

    return random.choice(hesitant_ if hesitant else fluent)


async def _run_demo_session(session_id: str, _campaign_id: int, interval: float = 2.0):
    """Auto-advance a session by sending simulated answers until complete.

    Alternates between fluent and hesitant answers so mirroring has signal to work with.
    """
    from .voice.stt.adapter import MockSTTAdapter

    session = _voice_sessions.get(session_id)
    if session:
        session["demo_running"] = True

    await asyncio.sleep(1.0)  # brief pause before first auto-turn
    turn_num = 0
    try:
        for _ in range(60):     # safety cap
            session = _voice_sessions.get(session_id)
            if not session:
                break
            ctx = session.get("ctx")
            if not ctx:
                break
            if session.get("completed_at"):
                break

            q = ctx.current_question
            # Phase 0 (turns 0-1): hesitant / slow — sets the calibration baseline at ~80 WPM
            # Phase 1 (turns 2+): fluent / fast — speaking_rate rises to ~1.2× as mirroring kicks in
            is_hesitant = turn_num < 2
            answer = _mock_answer_for_question(
                q.question_type if q else "free_text",
                q.config if q else {},
                str(ctx.state),
                language=getattr(ctx, "_language", "en") or "en",
                hesitant=is_hesitant,
            )

            # Hesitant: slow speech ~80 WPM (750ms/word)
            # Fluent:   fast speech ~220 WPM (270ms/word)
            ms_per_word = 750.0 if is_hesitant else 270.0

            _pipeline._stt = MockSTTAdapter(
                responses=[answer],
                confidence=_estimate_mock_confidence(answer),
                ms_per_word=ms_per_word,
            )

            async def _chunks(text=answer):
                yield text.encode()

            try:
                result = await _pipeline.process_turn(ctx, _chunks())
                session["stt_metrics"].append(result.stt_metrics)
                session["tts_metrics"].append(result.tts_metrics)

                if result.mirroring_decision is not None:
                    md = result.mirroring_decision
                    session["last_mirroring"] = {
                        **md.to_dict(),
                        "monitoring_flags": _pipeline._mirroring_policy.monitoring_flags(
                            ctx.mirroring_calibration,
                            _pipeline._compute_rapport(ctx),
                        ),
                        "calibration": ctx.mirroring_calibration.to_dict(),
                    }

                if result.session_complete:
                    session["completed_at"] = datetime.now(timezone.utc).isoformat()
                    session["answers"] = dict(ctx.answers)
                    break
            except Exception:
                break

            turn_num += 1
            await asyncio.sleep(interval)
    finally:
        session = _voice_sessions.get(session_id)
        if session:
            session["demo_running"] = False


@app.post("/api/sessions/{session_id}/demo-run")
async def start_demo_run(
    session_id: str,
    interval: float = Query(default=7.0, ge=1.0, le=30.0),
):
    """Start auto-advancing a session with simulated participant answers.

    interval: seconds between turns (default 7, range 1–30).
    """
    session = _voice_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Voice session not found")

    if session_id in _demo_run_tasks and not _demo_run_tasks[session_id].done():
        return {"status": "already_running", "session_id": session_id}

    task = asyncio.create_task(_run_demo_session(session_id, session["campaign_id"], interval=interval))
    _demo_run_tasks[session_id] = task
    return {"status": "started", "session_id": session_id}


@app.delete("/api/operator-queue/{session_id}")
def resolve_operator_queue_item(session_id: str) -> dict:
    """Remove a session from the operator queue once handled."""
    global _operator_queue
    before = len(_operator_queue)
    _operator_queue = [item for item in _operator_queue if item["session_id"] != session_id]
    if len(_operator_queue) == before:
        raise HTTPException(status_code=404, detail="Session not found in operator queue")
    return {"resolved": True, "session_id": session_id}
