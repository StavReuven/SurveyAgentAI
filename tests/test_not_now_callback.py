"""Regression test: saying "not now, call me later" must be recorded as
'not_now' (not 'completed'), and the scheduler must retry soon via
retry_delay_minutes rather than waiting out the long cooldown_hours."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from auth_helpers import login_admin


def _setup_campaign(client):
    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Not Now Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]
    client.post(
        f"/api/campaigns/{campaign_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(f"/api/campaigns/{campaign_id}/start")
    return campaign_id


def test_not_now_recorded_as_not_now_not_completed(client):
    campaign_id = _setup_campaign(client)
    session = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions",
        json={"participant_phone": "+15550001111", "locale": "en-US"},
    ).json()
    session_id = session["session_id"]

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "not now, call me back later", "audio_duration_ms": 500},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_complete"] is True

    from app.database import get_db
    from app.main import app
    from app.models import CallLog

    db = next(app.dependency_overrides[get_db]())
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    assert call_log.status == "not_now"


def test_not_now_outcome_retries_soon_not_after_long_cooldown(client):
    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Retry Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]

    from app.database import get_db
    from app.main import app, _next_attempt_eligible, _utcnow
    from app.models import CallAttempt, CallingPolicy, Participant

    db = next(app.dependency_overrides[get_db]())
    participant = Participant(campaign_id=campaign_id, phone_number="+15550002222")
    db.add(participant)
    policy = CallingPolicy(
        campaign_id=campaign_id, window_start_hour=0, window_end_hour=23,
        max_attempts=5, retry_delay_minutes=2, cooldown_hours=24,
        max_calls_per_minute=10, enabled=True,
    )
    db.add(policy)
    db.commit()

    finished_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    attempt = CallAttempt(
        campaign_id=campaign_id, participant_id=participant.id, attempt_number=1,
        outcome="not_now", started_at=finished_at, finished_at=finished_at,
    )
    db.add(attempt)
    db.commit()

    eligible, _ = _next_attempt_eligible(db, campaign_id, participant, policy, _utcnow())
    assert eligible is True, "5 minutes after a not_now outcome (2min retry_delay) should already be eligible"


def test_requested_callback_time_is_captured_from_transcript(client):
    """When the caller names a specific time ("call me tomorrow evening"),
    that must be stored on the participant — not just the generic 'not_now'
    outcome — so the scheduler can honor the actual requested time instead
    of always using the short retry_delay_minutes default."""
    campaign_id = _setup_campaign(client)
    session = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions",
        json={"participant_phone": "+15550003333", "locale": "en-US"},
    ).json()
    session_id = session["session_id"]

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "call me back tomorrow evening", "audio_duration_ms": 500},
    )
    assert resp.status_code == 200
    assert resp.json()["session_complete"] is True

    # This session had no participant_id (started via the manual voice-session
    # API, not the scheduler), so there's nothing to attach the time to — the
    # important thing is it doesn't crash and the call is still recorded as
    # not_now, exactly like the plain "not now" case above.
    from app.database import get_db
    from app.main import app
    from app.models import CallLog

    db = next(app.dependency_overrides[get_db]())
    call_log = db.query(CallLog).filter(CallLog.session_id == session_id).first()
    assert call_log.status == "not_now"


def test_requested_callback_time_overrides_generic_retry_delay(client):
    """End-to-end: dial a real (scheduler-linked) participant, have them ask
    for a callback ~24h out, and confirm the scheduler actually waits for
    that time instead of the short generic retry_delay_minutes."""
    from unittest.mock import AsyncMock, patch

    from app.database import get_db
    from app.main import app, _process_scheduler_tick, _next_attempt_eligible, _utcnow
    from app.models import CallAttempt, CallingPolicy, CampaignExecution, Participant

    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Callback Time Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]
    client.post(
        f"/api/campaigns/{campaign_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(
        f"/api/campaigns/{campaign_id}/participants/upload",
        files={"file": ("p.csv", "phone_number,full_name,locale\n+15550004444,Test,en-US\n", "text/csv")},
    )
    client.post(f"/api/campaigns/{campaign_id}/start")

    db = next(app.dependency_overrides[get_db]())
    execution = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).first()
    fake_session = type("S", (), {"call_sid": "CAfake", "session_id": None})()
    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=fake_session)
        import asyncio
        asyncio.run(_process_scheduler_tick(db, execution))
        db.commit()

    attempt = db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).first()
    session_id = attempt.session_id

    client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "call me back tomorrow evening", "audio_duration_ms": 500},
    )

    # process_voice_turn only updates CallLog.status — CallAttempt.outcome/
    # finished_at are normally filled in later by the Twilio status webhook
    # (see webhook_status), which this test doesn't simulate. Set them
    # directly here, exactly as the webhook would, so _next_attempt_eligible
    # (the thing actually under test) isn't blocked by an unrelated
    # "still in progress" check (finished_at is None → not eligible).
    db.refresh(attempt)
    attempt.outcome = "not_now"
    attempt.finished_at = _utcnow()
    db.commit()

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    db.refresh(participant)
    assert "requested_callback_at" in (participant.meta or {}), (
        "expected the parsed callback time to be stored on participant.meta"
    )
    # "tomorrow evening" always lands at hour 19 the next calendar day, but
    # how far that is from "now" depends on what time this test happens to
    # run — anywhere from ~19h to ~43h out. Read the actual stored value
    # instead of guessing a fixed offset.
    stored_callback_at = datetime.fromisoformat(participant.meta["requested_callback_at"])

    db.refresh(attempt)
    # Tighten the generic retry so it's trivially satisfied — the test only
    # means something if the callback-time override (not an accidentally
    # strict default policy) is why eligibility stays False below.
    policy = db.query(CallingPolicy).filter(CallingPolicy.campaign_id == campaign_id).first()
    policy.retry_delay_minutes = 1
    db.commit()
    db.refresh(policy)

    # Well past the generic 1-minute retry delay, but nowhere near "tomorrow
    # evening" — must NOT be eligible yet if the requested time is honored.
    soon = _utcnow() + timedelta(minutes=10)
    eligible_soon, _ = _next_attempt_eligible(db, campaign_id, participant, policy, soon)
    assert eligible_soon is False, (
        "should still be waiting for the caller-requested time, not the generic retry delay"
    )

    # Just past the actual stored callback time — must now be eligible.
    much_later = stored_callback_at + timedelta(minutes=1)
    eligible_later, _ = _next_attempt_eligible(db, campaign_id, participant, policy, much_later)
    assert eligible_later is True


def test_stale_requested_callback_time_does_not_leak_into_next_cycle(client):
    """Edge case: after the caller-requested callback time is actually acted
    on (the scheduler redials), it must be consumed — not left behind to
    incorrectly override the generic retry policy again on a LATER attempt
    that has nothing to do with the original request."""
    from unittest.mock import AsyncMock, patch

    from app.database import get_db
    from app.main import app, _process_scheduler_tick, _dial_participant, _next_attempt_eligible, _utcnow
    from app.models import Campaign, CallAttempt, CallingPolicy, CampaignExecution, Participant

    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Stale Callback Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]
    client.post(
        f"/api/campaigns/{campaign_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(
        f"/api/campaigns/{campaign_id}/participants/upload",
        files={"file": ("p.csv", "phone_number,full_name,locale\n+15550005555,Test,en-US\n", "text/csv")},
    )
    client.post(f"/api/campaigns/{campaign_id}/start")

    db = next(app.dependency_overrides[get_db]())
    execution = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).first()

    # Attempt 1: dial, caller names a specific callback time.
    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=type("S", (), {"call_sid": "CA1", "session_id": None})())
        import asyncio
        asyncio.run(_process_scheduler_tick(db, execution))
        db.commit()

    attempt1 = db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).first()
    client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{attempt1.session_id}/turn",
        json={"transcript": "call me back tomorrow evening", "audio_duration_ms": 500},
    )
    db.refresh(attempt1)
    attempt1.outcome = "not_now"
    attempt1.finished_at = _utcnow()
    db.commit()

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    db.refresh(participant)
    assert "requested_callback_at" in participant.meta

    # Attempt 2: the scheduler actually redials (simulating the requested
    # time having arrived) — _dial_participant must consume/clear the entry.
    campaign_row = db.get(Campaign, campaign_id)
    asyncio.run(_dial_participant(db, campaign_row, participant, attempt_number=2))
    db.commit()
    db.refresh(participant)
    assert "requested_callback_at" not in (participant.meta or {}), (
        "the one-time callback request must be consumed once acted on"
    )

    attempt2 = (
        db.query(CallAttempt)
        .filter(CallAttempt.campaign_id == campaign_id, CallAttempt.attempt_number == 2)
        .first()
    )
    # This time the caller just says "not now" again — no time named.
    client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{attempt2.session_id}/turn",
        json={"transcript": "not now", "audio_duration_ms": 500},
    )
    db.refresh(attempt2)
    attempt2.outcome = "not_now"
    attempt2.finished_at = _utcnow()
    db.commit()
    db.refresh(participant)
    assert "requested_callback_at" not in (participant.meta or {})

    # Attempt 3's eligibility must now fall back to the generic policy delay
    # — not be blocked waiting for the long-gone "tomorrow evening" request.
    policy = db.query(CallingPolicy).filter(CallingPolicy.campaign_id == campaign_id).first()
    soon_after_generic_delay = _utcnow() + timedelta(minutes=policy.retry_delay_minutes + 1)
    eligible, _ = _next_attempt_eligible(db, campaign_id, participant, policy, soon_after_generic_delay)
    assert eligible is True
