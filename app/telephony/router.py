"""SAA-42: Webhook handlers for Twilio voice and status callbacks."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from twilio.twiml.voice_response import VoiceResponse, Gather, Say

from ..database import get_db
from ..models import CallAttempt, CallLog, Participant
from ..settings.dnc import is_blocked
from .conference import (
    conference_name_for,
    generate_access_token,
    is_webrtc_configured,
)
from .config import TWILIO_WEBHOOK_BASE_URL
from .gateway import get_gateway
from .persistence import save_call_log
from .session_store import CallState, get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telephony", tags=["telephony"])

# Twilio's default <Say> voice set does not include Hebrew — it must be
# requested explicitly via a Google-engine voice name, or Hebrew text is
# silently not spoken (Gather still works, but no audio is heard).
_HEBREW_VOICE = "Google.he-IL-Standard-A"


def _say_kwargs(twiml_lang: str) -> dict:
    return {"language": twiml_lang, "voice": _HEBREW_VOICE} if twiml_lang == "he-IL" else {"language": twiml_lang}


def _build_say(text: str, twiml_lang: str, speaking_rate: float = 1.0, pitch_semitones: float = 0.0) -> Say:
    """Build a <Say> verb with an SSML <prosody> child so the Psycho-Adaptive
    Voice Mirroring rate/pitch the pipeline computes is actually audible on
    real calls (a plain gather.say(text) call ignores mirroring entirely)."""
    say = Say(**_say_kwargs(twiml_lang))
    rate_pct = max(50, min(200, round(speaking_rate * 100)))
    say.prosody(text, rate=f"{rate_pct}%", pitch=f"{pitch_semitones:+.1f}st")
    return say

# ── shared voice-session registry (injected from main at startup) ────────────
_voice_sessions: dict[str, dict] = {}


def set_voice_sessions_store(store: dict[str, dict]) -> None:
    """Called once from main.py to wire the in-memory pipeline session dict,
    so Twilio TwiML handlers can speak the pipeline's actual next question
    instead of a generic hardcoded line."""
    global _voice_sessions
    _voice_sessions = store


# ── SAA-41: Outbound call API ──────────────────────────────────────────────

@router.post("/calls")
async def initiate_call(
    to_number: str,
    campaign_id: int,
    session_id: str,
    db: Session = Depends(get_db),
):
    """Trigger an outbound Twilio call for an existing voice session."""
    if is_blocked(db, to_number):
        raise HTTPException(status_code=403, detail="Phone number is on the Do-Not-Call list")

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

    # Speak the pipeline's actual pre-computed greeting + first question
    # (set by POST /api/campaigns/{id}/voice/sessions) instead of a generic
    # line, so the caller is actually asked the campaign's real first question.
    pipeline_session = _voice_sessions.get(session_id)
    fallback_greeting = "Hello! Thank you for answering. Let us begin the survey." if not is_hebrew else "שלום! תודה שהקדשת מזמנך. נתחיל בסקר."
    greeting_text = (
        pipeline_session["last_response_text"]
        if pipeline_session and pipeline_session.get("last_response_text")
        else fallback_greeting
    )
    no_response_text = "We did not receive a response. Goodbye." if not is_hebrew else "לא קיבלנו תגובה. להתראות."

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=gather_url,
        method="POST",
        language=twiml_lang,
        speech_timeout="1",
        timeout=8,
        action_on_empty_result=True,
    )
    gather.append(_build_say(greeting_text, twiml_lang))
    vr.append(gather)
    vr.append(_build_say(no_response_text, twiml_lang))

    return Response(content=str(vr), media_type="application/xml")


@router.post("/webhook/resume", response_class=Response)
async def webhook_resume(
    session_id: str = Query(...),
    campaign_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Twilio calls this when a live call is redirected out of the operator
    Conference back to the AI agent (after 'return to agent'). Re-asks the
    session's current question via the pipeline's [resume] turn instead of
    leaving the caller in dead air or restarting the survey from scratch."""
    from ..models import Campaign
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    lang = getattr(campaign, "language", "en") if campaign else "en"
    is_hebrew = lang.startswith("he")
    twiml_lang = "he-IL" if is_hebrew else "en-US"

    next_action_url = (
        f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/gather"
        f"?session_id={session_id}&campaign_id={campaign_id}"
    )

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:8000/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
                json={"transcript": "[resume]", "confidence": 1.0},
                timeout=10,
            )
            data = resp.json()
    except Exception as exc:
        logger.error("Resume pipeline error: %s", exc)
        data = {
            "response_text": "נמשיך בסקר." if is_hebrew else "Let's continue the survey.",
            "session_complete": False,
        }

    response_text = data.get("response_text", "")
    no_response_text = "לא קיבלנו תגובה. להתראות." if is_hebrew else "We did not receive a response. Goodbye."

    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=next_action_url,
        method="POST",
        language=twiml_lang,
        speech_timeout="1",
        timeout=8,
        action_on_empty_result=True,
    )
    gather.append(_build_say(response_text, twiml_lang))
    vr.append(gather)
    vr.append(_build_say(no_response_text, twiml_lang))
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
            speech_timeout="1",
            timeout=8,
            action_on_empty_result=True,
        )
        gather.append(_build_say(retry_text, twiml_lang))
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

    # Psycho-Adaptive Voice Mirroring: apply the rate/pitch the pipeline just
    # computed for this turn to the actual spoken audio (see _build_say).
    mirroring = data.get("mirroring") or {}
    speaking_rate = mirroring.get("speaking_rate", 1.0) or 1.0
    pitch = mirroring.get("pitch", 0.0) or 0.0

    no_response_text = "לא קיבלנו תגובה. להתראות." if is_hebrew else "We did not receive a response. Goodbye."

    if session_complete:
        vr.append(_build_say(response_text, twiml_lang, speaking_rate, pitch))
        vr.hangup()
    else:
        gather = Gather(
            input="speech",
            action=next_action_url,
            method="POST",
            language=twiml_lang,
            speech_timeout="1",
            timeout=8,
            action_on_empty_result=True,
        )
        gather.append(_build_say(response_text, twiml_lang, speaking_rate, pitch))
        vr.append(gather)
        vr.append(_build_say(no_response_text, twiml_lang))

    logger.info("DEBUG gather response ready session=%s", session_id)
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

        # Auto-dial scheduler: finalize the CallAttempt this call was placed
        # for once it actually ends — it started as "pending" since a real
        # call's outcome isn't known until it rings/connects/finishes.
        _TERMINAL_STATES = {
            CallState.COMPLETED, CallState.FAILED, CallState.NO_ANSWER,
            CallState.BUSY, CallState.TIMEOUT,
        }
        if session.state in _TERMINAL_STATES:
            attempt = (
                db.query(CallAttempt)
                .filter(CallAttempt.session_id == session.session_id, CallAttempt.outcome == "pending")
                .first()
            )
            if attempt:
                outcome = "success" if session.state == CallState.COMPLETED else "failed"
                # Twilio only tells us the call connected and ended normally —
                # it can't distinguish "finished the whole survey" from "caller
                # asked to be called back later". That distinction lives in the
                # dialogue's own CallLog.status, set by process_voice_turn.
                if outcome == "success":
                    call_log = db.query(CallLog).filter(CallLog.session_id == session.session_id).first()
                    if call_log and call_log.status == "not_now":
                        outcome = "not_now"

                attempt.outcome = outcome
                attempt.finished_at = session.ended_at or datetime.now(timezone.utc)
                attempt.note = f"twilio_status={session.state.value}"
                participant = db.get(Participant, attempt.participant_id)
                if participant:
                    if outcome == "success":
                        participant.status = "contacted"
                    elif outcome == "not_now":
                        # Keep eligible for a quick retry (see _next_attempt_eligible)
                        # instead of treating this like a fully answered survey.
                        participant.status = "pending"
                    else:
                        participant.status = "failed"
                    meta = participant.meta or {}
                    meta["last_call_outcome"] = outcome
                    meta["last_call_note"] = attempt.note
                    participant.meta = meta
                db.commit()

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


# ── WebRTC / Conference endpoints ──────────────────────────────────────────

@router.get("/token")
async def get_access_token(identity: str = "operator"):
    """Return a Twilio Access Token for the browser Twilio.Device (Voice SDK).

    The token grants the holder permission to make outgoing calls through
    the configured TwiML App, which routes them to a Conference room.
    """
    if not is_webrtc_configured():
        return {
            "token": None,
            "configured": False,
            "message": "Twilio WebRTC not configured — set TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_TWIML_APP_SID in .env",
        }
    token = generate_access_token(identity)
    return {"token": token, "configured": True, "identity": identity}


@router.post("/conference-twiml", response_class=Response)
async def conference_twiml(
    request: Request,
    room: str | None = Query(default=None),
    role: str | None = Query(default=None),
    campaign_id: int | None = Query(default=None),
):
    """TwiML App Voice URL — called by Twilio when browser Device.connect() fires.

    Also used to redirect a live caller into the same Conference room.
    Both the browser and the original caller end up in <Conference name=room>.

    The caller's leg is redirected via the REST API with `room`/`role`/
    `campaign_id` in the URL query string, but Device.connect({params: {...}})
    on the operator's browser leg sends them as POST form fields instead — so
    we must accept either, or the operator's leg silently never joins.

    `endConferenceOnExit` is set ONLY for the operator's leg: if it applied to
    both legs, the operator leaving to "return to agent" would end the whole
    conference and disconnect the caller too. Only a real operator hangup
    should end things for both sides.

    The caller's <Dial> also gets an `action` URL back to the bot: Twilio
    refuses to redirect a call's URL directly while it's inside an active
    <Dial>, so 'return to agent' instead removes the caller's participant
    (see remove_caller_from_conference) — Twilio then fetches this action URL
    for follow-up TwiML instead of just hanging up.
    """
    from twilio.twiml.voice_response import VoiceResponse, Dial, Conference as TwiConference

    if not room or not role or not campaign_id:
        form = await request.form()
        room = room or form.get("room")
        role = role or form.get("role")
        campaign_id = campaign_id or form.get("campaign_id")
    if not room:
        vr = VoiceResponse()
        vr.say("Missing conference room.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    is_operator_leg = role == "operator"
    session_id = room.removeprefix("conf-")

    dial_kwargs: dict = {}
    if not is_operator_leg and campaign_id:
        dial_kwargs["action"] = (
            f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/resume"
            f"?session_id={session_id}&campaign_id={campaign_id}"
        )
        dial_kwargs["method"] = "POST"

    vr = VoiceResponse()
    dial = Dial(**dial_kwargs)
    # startConferenceOnEnter=True so operator can enter even if caller hasn't joined yet
    dial.conference(
        room,
        start_conference_on_enter=True,
        end_conference_on_exit=is_operator_leg,
        beep=False,
        wait_url="",          # silence instead of hold music
    )
    vr.append(dial)
    return Response(content=str(vr), media_type="application/xml")
