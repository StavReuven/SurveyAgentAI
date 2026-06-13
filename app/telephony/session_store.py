"""SAA-45: Session store — maps Twilio call_sid ↔ internal session_id."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional


class CallState(str, Enum):
    INITIATED  = "initiated"
    RINGING    = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED  = "completed"
    FAILED     = "failed"
    TIMEOUT    = "timeout"
    NO_ANSWER  = "no_answer"
    BUSY       = "busy"


@dataclass
class TelephonySession:
    call_sid: str
    session_id: str          # internal voice-pipeline session
    campaign_id: int
    participant_phone: str
    state: CallState = CallState.INITIATED
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None

    def apply_event(self, event: str, **kwargs) -> None:
        """SAA-46: event reducer — mutate state based on Twilio status callback."""
        mapping = {
            "initiated":   CallState.INITIATED,
            "ringing":     CallState.RINGING,
            "in-progress": CallState.IN_PROGRESS,
            "completed":   CallState.COMPLETED,
            "failed":      CallState.FAILED,
            "no-answer":   CallState.NO_ANSWER,
            "busy":        CallState.BUSY,
        }
        new_state = mapping.get(event)
        if new_state:
            self.state = new_state

        if new_state == CallState.IN_PROGRESS:
            self.answered_at = datetime.now(timezone.utc)

        if new_state in (CallState.COMPLETED, CallState.FAILED,
                         CallState.NO_ANSWER, CallState.BUSY):
            self.ended_at = datetime.now(timezone.utc)
            if self.answered_at:
                delta = self.ended_at - self.answered_at
                self.duration_seconds = int(delta.total_seconds())

        if "error" in kwargs:
            self.error_message = kwargs["error"]


class SessionStore:
    """In-memory store with call_sid and session_id indexes."""

    def __init__(self) -> None:
        self._by_call_sid: Dict[str, TelephonySession] = {}
        self._by_session_id: Dict[str, TelephonySession] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: TelephonySession) -> None:
        async with self._lock:
            self._by_call_sid[session.call_sid] = session
            self._by_session_id[session.session_id] = session

    async def get_by_call_sid(self, call_sid: str) -> Optional[TelephonySession]:
        return self._by_call_sid.get(call_sid)

    async def get_by_session_id(self, session_id: str) -> Optional[TelephonySession]:
        return self._by_session_id.get(session_id)

    async def update_state(self, call_sid: str, event: str, **kwargs) -> Optional[TelephonySession]:
        async with self._lock:
            session = self._by_call_sid.get(call_sid)
            if session:
                session.apply_event(event, **kwargs)
            return session

    async def all_active(self) -> list[TelephonySession]:
        active = {CallState.INITIATED, CallState.RINGING, CallState.IN_PROGRESS}
        return [s for s in self._by_call_sid.values() if s.state in active]

    async def remove(self, call_sid: str) -> None:
        async with self._lock:
            session = self._by_call_sid.pop(call_sid, None)
            if session:
                self._by_session_id.pop(session.session_id, None)


# Singleton used across the app
_store = SessionStore()

def get_store() -> SessionStore:
    return _store
