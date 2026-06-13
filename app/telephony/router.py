"""SAA-42: Webhook handlers for Twilio voice and status callbacks."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.voice_response import VoiceResponse, Gather

from ..database import get_db
from .config import TWILIO_WEBHOOK_BASE_URL
from .gateway import get_gateway
from .persistence import save_call_log
from .session_store import get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telephony", tags=["telephony"])


# ── SAA-41: Outbound call API ──────────────────────────────────────────────

@router.post("/calls")
async def initiate_call(
    to_number: str,
    campaign_id: int,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Trigger an outbound Twilio call for an existing voice session."""
    store   = get_store()
    gateway = get_gateway()

    tel_session = await gateway.initiate_call(
        to_number=to_number,
        campaign_id=campaign_id,
        session_id=session_id,
        store=store,
    )
    save_call_log(db, tel_session)

    return {
        "call_sid":   tel_session.call_sid,
        "session_id": tel_session.session_id,
        "state":      tel_session.state.value,
    }


@router.delete("/calls/{call_sid}")
async def hangup_call(call_sid: str):
    """Hang up an active call."""
    success = await get_gateway().hangup(call_sid)
    return {"hung_up": success}


# ── SAA-42: Webhook — voice (TwiML) ───────────────────────────────────────

@router.post("/webhook/voice", response_class=Response)
async def webhook_voice(
    session_id: str = Query(...),
    campaign_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """
    Twilio calls this when the participant answers.
    Fetches the first greeting from the voice session and returns TwiML.
    """
    import httpx
    gather_url = (
        f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/gather"
        f"?session_id={session_id}&campaign_id={campaign_id}"
    )

    from ..models import Campaign
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    lang = (campaign.language or "en") if campaign else "en"
    is_hebrew = lang.startswith("he")
    twiml_lang = "he-IL" if is_hebrew else "en-US"
    greeting_text = "Hello! Thank you for answering. Let us begin the survey." if not is_hebrew else "Shalom! Todah she-anita. Matchilim et haseker."
    no_response_text = "We did not receive a response. Goodbye." if not is_hebrew else "Lo kibalnu tguvah. Lehitraot."

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=gather_url,
        method="POST",
        language=twiml_lang,
        speech_timeout="3",
        timeout=8,
    )
    gather.say(greeting_text, language=twiml_lang)
    vr.append(gather)
    vr.say(no_response_text, language=twiml_lang)

    return Response(content=str(vr), media_type="application/xml")


@router.post("/webhook/gather", response_class=Response)
async def webhook_gather(
    session_id: str = Query(...),
    campaign_id: int = Query(...),
    SpeechResult: Optional[str] = Form(None),
    Confidence: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Twilio posts the participant's speech here after each Gather.
    We forward to the voice pipeline and respond with the next question.
    """
    logger.info(
        "gather session=%s speech=%r confidence=%s",
        session_id, SpeechResult, Confidence,
    )

    # Get campaign language
    from ..models import Campaign
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    lang = getattr(campaign, "language", "en") if campaign else "en"
    is_hebrew = lang.startswith("he")
    twiml_lang = "he-IL" if is_hebrew else "en-US"

    next_action_url = (
        f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/gather"
        f"?session_id={session_id}&campaign_id={campaign_id}"
    )

    vr = VoiceResponse()

    if not SpeechResult:
        retry_text = "לא הצלחתי לשמוע. אנא נסה שוב." if is_hebrew else "I could not hear you. Please try again."
        gather = Gather(
            input="speech",
            action=next_action_url,
            method="POST",
            language=twiml_lang,
            speech_timeout="3",
            timeout=8,
        )
        gather.say(retry_text, language=twiml_lang)
        vr.append(gather)
        return Response(content=str(vr), media_type="application/xml")

    # Forward speech to internal voice pipeline via HTTP
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:8000/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
                json={"transcript": SpeechResult, "confidence": Confidence or 0.8},
                timeout=10,
            )
            data = resp.json()
    except Exception as exc:
        logger.error("Pipeline error: %s", exc)
        error_text = "אירעה שגיאה. נסה שוב." if is_hebrew else "An error occurred. Please try again."
        data = {"response_text": error_text, "session_complete": False}

    response_text    = data.get("response_text", "")
    session_complete = data.get("session_complete", False)

    no_response_text = "לא קיבלנו תגובה. להתראות." if is_hebrew else "We did not receive a response. Goodbye."

    if session_complete:
        vr.say(response_text, language=twiml_lang)
        vr.hangup()
    else:
        gather = Gather(
            input="speech",
            action=next_action_url,
            method="POST",
            language=twiml_lang,
            speech_timeout="3",
            timeout=8,
        )
        gather.say(response_text, language=twiml_lang)
        vr.append(gather)
        vr.say(no_response_text, language=twiml_lang)

    return Response(content=str(vr), media_type="application/xml")


# ── SAA-42: Webhook — status callback ─────────────────────────────────────

@router.post("/webhook/status")
async def webhook_status(
    CallSid:      str            = Form(...),
    CallStatus:   str            = Form(...),
    CallDuration: Optional[str]  = Form(None),
    db: Session = Depends(get_db),
):
    """
    Twilio posts call lifecycle events here (ringing, in-progress, completed…).
    Updates session store and persists to DB.
    """
    logger.info("status callback call_sid=%s status=%s", CallSid, CallStatus)

    store = get_store()
    session = await store.update_state(CallSid, CallStatus)

    if session:
        if CallDuration and session.duration_seconds is None:
            try:
                session.duration_seconds = int(CallDuration)
            except ValueError:
                pass
        save_call_log(db, session)

    return {"received": True}


# ── Active calls list ──────────────────────────────────────────────────────

@router.get("/calls")
async def list_active_calls():
    """Return all currently active telephony sessions."""
    store = get_store()
    active = await store.all_active()
    return {
        "calls": [
            {
                "call_sid":         s.call_sid,
                "session_id":       s.session_id,
                "campaign_id":      s.campaign_id,
                "participant_phone": s.participant_phone,
                "state":            s.state.value,
                "started_at":       s.started_at.isoformat(),
            }
            for s in active
        ]
    }
