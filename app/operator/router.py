"""SAA-94: Operator Console API — queue, takeover, return, audit trail."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..voice.escalation import get_escalation_queue
from .audit import OperatorAction, get_audit_log

router = APIRouter(prefix="/api/operator", tags=["operator"])


# ── SAA-89: Priority queue view ───────────────────────────────────────────

@router.get("/queue")
async def get_queue():
    """Return escalated sessions sorted by urgency (highest first)."""
    queue = get_escalation_queue()
    return {
        "count": len(queue),
        "sessions": [s.to_dict() for s in queue.all_sorted()],
    }


@router.get("/queue/{session_id}")
async def get_queue_session(session_id: str):
    """Get a single escalated session by ID."""
    snap = get_escalation_queue().get(session_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Session not in escalation queue")
    return snap.to_dict()


# ── SAA-97: Control actions ───────────────────────────────────────────────

class TakeoverRequest(BaseModel):
    operator_id: str


@router.post("/sessions/{session_id}/takeover")
async def takeover_session(session_id: str, body: TakeoverRequest):
    """Operator takes control of an escalated call."""
    queue = get_escalation_queue()
    snap = queue.get(session_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Session not in escalation queue")
    if snap.operator_id is not None:
        raise HTTPException(status_code=409, detail=f"Already taken over by {snap.operator_id}")

    snap.operator_id = body.operator_id
    snap.taken_over_at = datetime.now()
    get_audit_log().record(session_id, body.operator_id, OperatorAction.TAKEOVER)

    return {"status": "taken_over", "session": snap.to_dict()}


@router.post("/sessions/{session_id}/return")
async def return_to_agent(session_id: str, body: TakeoverRequest):
    """Operator returns control to the AI agent."""
    queue = get_escalation_queue()
    snap = queue.get(session_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Session not in escalation queue")

    snap.returned_at = datetime.now()
    snap.operator_id = None
    get_audit_log().record(session_id, body.operator_id, OperatorAction.RETURN_TO_AGENT)
    queue.remove(session_id)

    return {"status": "returned_to_agent", "session_id": session_id}


@router.post("/sessions/{session_id}/hangup")
async def hangup_session(session_id: str, body: TakeoverRequest):
    """Operator ends the call."""
    get_escalation_queue().remove(session_id)
    get_audit_log().record(session_id, body.operator_id, OperatorAction.HANGUP)
    return {"status": "hung_up", "session_id": session_id}


# ── SAA-96: Transcript view ────────────────────────────────────────────────

@router.get("/sessions/{session_id}/transcript")
async def get_transcript(session_id: str):
    """Return the conversation history captured in the escalation snapshot."""
    snap = get_escalation_queue().get(session_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Session not in escalation queue")
    return {
        "session_id": session_id,
        "transcript": snap.history,
        "answers_so_far": snap.answers_so_far,
    }


# ── SAA-98: Audit trail ────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit(session_id: str | None = None):
    """Return audit log entries, optionally filtered by session."""
    log = get_audit_log()
    entries = (
        log.get_for_session(session_id) if session_id else log.get_all()
    )
    return {"entries": [e.to_dict() for e in entries]}
