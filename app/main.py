import csv
import io
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import BranchRule, Campaign, Participant, Question
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
