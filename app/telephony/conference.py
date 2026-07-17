"""Twilio Conference helpers for operator WebRTC takeover.

Flow:
  1. Real Twilio call is in progress (call_sid known).
  2. Operator clicks "השתלט" → backend calls redirect_call_to_conference().
  3. Twilio redirects the live call to <Conference name="conf-{session_id}">.
  4. Browser fetches an Access Token → Twilio.Device.connect() →
     hits /api/telephony/conference-twiml → same Conference room.
  5. Both parties are now in the same conference and can speak.
"""
from __future__ import annotations

import logging

from .config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_API_KEY,
    TWILIO_API_SECRET,
    TWILIO_AUTH_TOKEN,
    TWILIO_TWIML_APP_SID,
    TWILIO_WEBHOOK_BASE_URL,
)

logger = logging.getLogger(__name__)


def is_webrtc_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_API_KEY and TWILIO_API_SECRET and TWILIO_TWIML_APP_SID)


def generate_access_token(identity: str) -> str:
    """Generate a short-lived Twilio Access Token with a Voice grant.

    The token authorises the browser Twilio.Device to make outgoing calls
    through the TwiML App whose SID is TWILIO_TWIML_APP_SID.
    """
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant

    token = AccessToken(
        TWILIO_ACCOUNT_SID,
        TWILIO_API_KEY,
        TWILIO_API_SECRET,
        identity=identity,
        ttl=3600,
    )
    grant = VoiceGrant(
        outgoing_application_sid=TWILIO_TWIML_APP_SID,
        incoming_allow=True,
    )
    token.add_grant(grant)
    return token.to_jwt()


def redirect_call_to_conference(call_sid: str, conference_name: str, campaign_id: int) -> bool:
    """Redirect a live Twilio call into a Conference room.

    Twilio will fetch the conference TwiML from our endpoint and place
    the caller in the named Conference, where the operator will join via WebRTC.

    `campaign_id` is threaded through so the caller's <Dial> gets an `action`
    URL back to the AI agent — see conference_twiml() — enabling 'return to
    agent' to work by removing the participant rather than a direct call
    redirect (which Twilio rejects while a call is inside an active Dial).
    """
    from twilio.rest import Client

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        url = (
            f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/conference-twiml"
            f"?room={conference_name}&campaign_id={campaign_id}"
        )
        client.calls(call_sid).update(url=url, method="POST")
        logger.info("Redirected call %s to conference %s", call_sid, conference_name)
        return True
    except Exception as exc:
        logger.error("Failed to redirect call %s: %s", call_sid, exc)
        return False


def remove_caller_from_conference(conference_name: str, call_sid: str) -> bool:
    """Remove the caller's participant from the Conference (used by 'return to
    agent'). Twilio cannot redirect a call's URL while it's inside an active
    <Dial> — but since the caller's <Dial> has an `action` URL configured
    (see conference_twiml), removing them from the conference makes Twilio
    fetch that action URL for follow-up TwiML instead of hanging up.
    """
    from twilio.rest import Client

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        conferences = client.conferences.list(friendly_name=conference_name, status="in-progress", limit=1)
        if not conferences:
            logger.error("No in-progress conference found for %s", conference_name)
            return False
        client.conferences(conferences[0].sid).participants(call_sid).delete()
        logger.info("Removed call %s from conference %s", call_sid, conference_name)
        return True
    except Exception as exc:
        logger.error("Failed to remove call %s from conference %s: %s", call_sid, conference_name, exc)
        return False


def conference_name_for(session_id: str) -> str:
    return f"conf-{session_id}"
