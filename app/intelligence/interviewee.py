"""Interviewee linking service.

Identifies a caller by phone number, creates or updates their Interviewee profile,
links all their Answers to the profile, and accumulates demographic data over time.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Answer, CallLog, EntityMention, Interviewee


def get_or_create_interviewee(phone_number: str, db: Session) -> Interviewee:
    """Return existing Interviewee or create a new one for this phone number."""
    interviewee = (
        db.query(Interviewee).filter(Interviewee.phone_number == phone_number).first()
    )
    if not interviewee:
        interviewee = Interviewee(
            phone_number=phone_number,
            demographics={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(interviewee)
        db.flush()
    return interviewee


def enrich_interviewee_from_session(
    session_id: str,
    participant_phone: str,
    db: Session,
) -> Interviewee:
    """
    Link all answers in this session to the Interviewee profile.
    Accumulate any PERSON entity detected as a name.
    """
    interviewee = get_or_create_interviewee(participant_phone, db)

    # Link answer rows to this interviewee
    db.query(Answer).filter(Answer.session_id == session_id).update(
        {"interviewee_id": interviewee.id}, synchronize_session="fetch"
    )

    # Try to extract name from PERSON entities in this session
    name_entity = (
        db.query(EntityMention)
        .filter(
            EntityMention.session_id == session_id,
            EntityMention.entity_type == "PERSON",
        )
        .order_by(EntityMention.confidence.desc())
        .first()
    )
    if name_entity and not interviewee.full_name:
        interviewee.full_name = name_entity.entity_value

    # Count total campaigns this person has participated in
    session_campaign_ids = (
        db.query(CallLog.campaign_id)
        .filter(
            CallLog.session_id.in_(
                db.query(Answer.session_id).filter(Answer.interviewee_id == interviewee.id)
            )
        )
        .distinct()
        .all()
    )
    demo = interviewee.demographics or {}
    demo["campaigns_count"] = len(session_campaign_ids)
    demo["total_answers"] = (
        db.query(Answer).filter(Answer.interviewee_id == interviewee.id).count()
    )
    interviewee.demographics = demo
    interviewee.updated_at = datetime.now(timezone.utc)
    db.flush()
    return interviewee


def build_interviewee_profiles(db: Session) -> int:
    """
    Bulk-link all CallLog sessions that have a participant_id to an Interviewee.
    Uses the participant's phone number. Returns count created/updated.
    """
    from ..models import Participant

    logs_with_participant = (
        db.query(CallLog, Participant.phone_number)
        .join(Participant, Participant.id == CallLog.participant_id)
        .filter(CallLog.participant_id.isnot(None))
        .all()
    )

    count = 0
    for log, phone in logs_with_participant:
        interviewee = get_or_create_interviewee(phone, db)
        # Link answers for this session
        updated = (
            db.query(Answer)
            .filter(Answer.session_id == log.session_id, Answer.interviewee_id.is_(None))
            .update({"interviewee_id": interviewee.id}, synchronize_session="fetch")
        )
        if updated:
            count += updated

    db.flush()
    return count
