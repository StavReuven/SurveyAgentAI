"""SAA-47: Timeout watchdog — marks stale sessions as timed-out."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .session_store import CallState, SessionStore

logger = logging.getLogger(__name__)

RINGING_TIMEOUT_SECONDS   = 30   # no answer within 30 s → timeout
IN_PROGRESS_TIMEOUT_SECONDS = 600  # call stuck in-progress for 10 min → timeout


async def _watchdog_loop(store: SessionStore, tick: int = 10) -> None:
    while True:
        await asyncio.sleep(tick)
        now = utcnow()
        try:
            for session in await store.all_active():
                age = (now - session.started_at).total_seconds()

                if session.state == CallState.RINGING and age > RINGING_TIMEOUT_SECONDS:
                    logger.warning("Call %s timed out (ringing)", session.call_sid)
                    session.state = CallState.TIMEOUT
                    session.ended_at = now

                elif session.state == CallState.IN_PROGRESS:
                    answered_at = session.answered_at or session.started_at
                    in_progress_age = (now - answered_at).total_seconds()
                    if in_progress_age > IN_PROGRESS_TIMEOUT_SECONDS:
                        logger.warning("Call %s timed out (in-progress)", session.call_sid)
                        session.state = CallState.TIMEOUT
                        session.ended_at = now
        except Exception:
            logger.exception("Error in telephony watchdog")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start_watchdog(store: SessionStore) -> asyncio.Task:
    return asyncio.create_task(_watchdog_loop(store))
