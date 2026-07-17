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
