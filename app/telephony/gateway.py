"""SAA-41: Outbound call API — wraps Twilio REST client."""
from __future__ import annotations

from typing import Optional

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from .config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    TWILIO_WEBHOOK_BASE_URL,
    is_configured,
)
from .session_store import CallState, SessionStore, TelephonySession


class TelephonyGateway:
    def __init__(self) -> None:
        self._client: Optional[Client] = None
        if is_configured():
            self._client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    async def initiate_call(
        self,
        to_number: str,
        campaign_id: int,
        session_id: str,
        store: SessionStore,
    ) -> TelephonySession:
        if not self._client:
            raise RuntimeError("Twilio is not configured. Check your .env file.")

        voice_url  = f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/voice?session_id={session_id}&campaign_id={campaign_id}"
        status_url = f"{TWILIO_WEBHOOK_BASE_URL}/api/telephony/webhook/status"

        try:
            call = self._client.calls.create(
                to=to_number,
                from_=TWILIO_PHONE_NUMBER,
                url=voice_url,
                status_callback=status_url,
                status_callback_method="POST",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                timeout=30,
            )
        except TwilioRestException as exc:
            raise RuntimeError(f"Twilio error: {exc.msg}") from exc

        tel_session = TelephonySession(
            call_sid=call.sid,
            session_id=session_id,
            campaign_id=campaign_id,
            participant_phone=to_number,
            state=CallState.INITIATED,
        )
        await store.add(tel_session)
        return tel_session

    async def hangup(self, call_sid: str) -> bool:
        if not self._client:
            return False
        try:
            self._client.calls(call_sid).update(status="completed")
            return True
        except TwilioRestException:
            return False


_gateway = TelephonyGateway()

def get_gateway() -> TelephonyGateway:
    return _gateway
