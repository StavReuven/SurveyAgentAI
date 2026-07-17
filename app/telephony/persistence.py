"""SAA-48: Persist telephony sessions to CallLog DB table."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import CallLog
from .session_store import CallState, TelephonySession

# Map telephony states to CallLog status enum values
_STATUS_MAP = {
    CallState.INITIATED:   "active",
    CallState.RINGING:     "active",
    CallState.IN_PROGRESS: "active",
    CallState.COMPLETED:   "completed",
    CallState.FAILED:      "failed",
    CallState.NO_ANSWER:   "failed",
    CallState.BUSY:        "failed",
    CallState.TIMEOUT:     "failed",
}


def save_call_log(db: Session, session: TelephonySession) -> CallLog:
    """Insert or update a CallLog row from a TelephonySession."""
    status = _STATUS_MAP.get(session.state, "active")

    existing = db.query(CallLog).filter(CallLog.session_id == session.session_id).first()

    if existing:
        # The dialogue pipeline (process_voice_turn) already recorded a more
        # specific final status ("not_now", "escalated", "completed") based on
        # *why* the call ended. Twilio's status callback only knows the raw
        # call lifecycle (ringing/completed/busy/...) and arrives after the
        # pipeline's own update — don't let it clobber that finer-grained
        # status back to a generic "completed"/"failed".
        if existing.status != "active":
            existing.ended_at = existing.ended_at or session.ended_at
            db.commit()
            return existing
        existing.status   = status
        existing.ended_at = session.ended_at
        db.commit()
        return existing

    log = CallLog(
        campaign_id=session.campaign_id,
        session_id=session.session_id,
        status=status,
        started_at=session.started_at,
        ended_at=session.ended_at,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
