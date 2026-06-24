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


def redirect_call_to_conference(call_sid: str, conference_name: str) -> bool:
    """Redirect a live Twilio call into a Conference room.

    Twilio will fetch the conference TwiML from our endpoint and place
    the caller in the named Conference, where the operator will join via WebRTC.
    """
    from twilio.rest import Client

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        url = (
            f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/conference-twiml"
            f"?room={conference_name}"
        )
        client.calls(call_sid).update(url=url, method="POST")
        logger.info("Redirected call %s to conference %s", call_sid, conference_name)
        return True
    except Exception as exc:
        logger.error("Failed to redirect call %s: %s", call_sid, exc)
        return False


def conference_name_for(session_id: str) -> str:
    return f"conf-{session_id}"
