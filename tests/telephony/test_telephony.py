"""SAA-43 + SAA-49: Telephony gateway and session orchestrator tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.telephony.session_store import CallState, SessionStore, TelephonySession
from app.telephony.timeouts import _watchdog_loop


# ── SAA-45: Session store ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_store_add_and_retrieve():
    store = SessionStore()
    session = TelephonySession(
        call_sid="CA123",
        session_id="sess-1",
        campaign_id=1,
        participant_phone="+972501234567",
    )
    await store.add(session)

    assert await store.get_by_call_sid("CA123") is session
    assert await store.get_by_session_id("sess-1") is session


@pytest.mark.asyncio
async def test_session_store_update_state():
    store = SessionStore()
    session = TelephonySession(
        call_sid="CA456", session_id="sess-2",
        campaign_id=1, participant_phone="+972509999999",
    )
    await store.add(session)

    updated = await store.update_state("CA456", "in-progress")
    assert updated.state == CallState.IN_PROGRESS
    assert updated.answered_at is not None


@pytest.mark.asyncio
async def test_session_store_all_active():
    store = SessionStore()
    for i, state_event in enumerate(["initiated", "ringing", "completed"]):
        s = TelephonySession(
            call_sid=f"CA{i}", session_id=f"sess-{i}",
            campaign_id=1, participant_phone="+972500000000",
        )
        await store.add(s)
        await store.update_state(f"CA{i}", state_event)

    active = await store.all_active()
    assert len(active) == 2  # initiated + ringing, not completed


# ── SAA-46: Event reducer ──────────────────────────────────────────────────

def test_event_reducer_full_lifecycle():
    session = TelephonySession(
        call_sid="CA789", session_id="sess-3",
        campaign_id=1, participant_phone="+972501111111",
    )
    session.apply_event("ringing")
    assert session.state == CallState.RINGING

    session.apply_event("in-progress")
    assert session.state == CallState.IN_PROGRESS
    assert session.answered_at is not None

    session.apply_event("completed")
    assert session.state == CallState.COMPLETED
    assert session.ended_at is not None
    assert session.duration_seconds is not None
    assert session.duration_seconds >= 0


def test_event_reducer_failed():
    session = TelephonySession(
        call_sid="CAabc", session_id="sess-4",
        campaign_id=1, participant_phone="+972502222222",
    )
    session.apply_event("failed", error="carrier failure")
    assert session.state == CallState.FAILED
    assert session.error_message == "carrier failure"


def test_event_reducer_no_answer():
    session = TelephonySession(
        call_sid="CAdef", session_id="sess-5",
        campaign_id=1, participant_phone="+972503333333",
    )
    session.apply_event("no-answer")
    assert session.state == CallState.NO_ANSWER


# ── SAA-43: Provider sandbox tests (mocked Twilio) ────────────────────────

@pytest.mark.asyncio
async def test_initiate_call_mock():
    """Verify gateway calls Twilio and creates a TelephonySession."""
    mock_call = MagicMock()
    mock_call.sid = "CAtest123"

    with patch("app.telephony.gateway.Client") as MockClient, \
         patch("app.telephony.gateway.is_configured", return_value=True):
        instance = MockClient.return_value
        instance.calls.create.return_value = mock_call

        from app.telephony.gateway import TelephonyGateway
        gw = TelephonyGateway()
        gw._client = instance

        store = SessionStore()
        tel = await gw.initiate_call(
            to_number="+972501234567",
            campaign_id=1,
            session_id="sess-mock",
            store=store,
        )

    assert tel.call_sid == "CAtest123"
    assert tel.state == CallState.INITIATED
    assert await store.get_by_call_sid("CAtest123") is tel


@pytest.mark.asyncio
async def test_hangup_mock():
    mock_call_resource = MagicMock()

    with patch("app.telephony.gateway.is_configured", return_value=True), \
         patch("app.telephony.gateway.Client") as MockClient:
        instance = MockClient.return_value
        instance.calls.return_value.update.return_value = None

        from app.telephony.gateway import TelephonyGateway
        gw = TelephonyGateway()
        gw._client = instance
        result = await gw.hangup("CAtest999")

    assert result is True


# ── SAA-47: Timeout watchdog ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchdog_marks_ringing_timeout():
    from datetime import datetime, timedelta, timezone
    store = SessionStore()
    session = TelephonySession(
        call_sid="CAtimeout", session_id="sess-timeout",
        campaign_id=1, participant_phone="+972504444444",
    )
    # Backdate started_at to simulate 60 seconds ago
    session.started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    session.state = CallState.RINGING
    await store.add(session)

    # Run one watchdog tick with very low threshold
    from app.telephony import timeouts as tw
    original = tw.RINGING_TIMEOUT_SECONDS
    tw.RINGING_TIMEOUT_SECONDS = 10  # 10s threshold, our session is 60s old
    try:
        task = asyncio.create_task(_watchdog_loop(store, tick=0.05))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        tw.RINGING_TIMEOUT_SECONDS = original

    assert session.state == CallState.TIMEOUT
