"""Tests for the campaign auto-dial scheduler placing real (mocked) Twilio calls
instead of instantly simulating an outcome."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.database import get_db
from app.main import app, _process_scheduler_tick
from app.models import CallAttempt, CampaignExecution, Participant
from auth_helpers import login_admin


def _db(client):
    return next(app.dependency_overrides[get_db]())


def _setup_running_campaign(client):
    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Auto Dial Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]
    client.post(
        f"/api/campaigns/{campaign_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(
        f"/api/campaigns/{campaign_id}/participants/upload",
        files={"file": ("p.csv", "phone_number,full_name,locale\n+15550001111,Test,en-US\n", "text/csv")},
    )
    resp = client.post(f"/api/campaigns/{campaign_id}/start")
    assert resp.status_code == 200, resp.text
    return campaign_id


@pytest.mark.asyncio
async def test_scheduler_places_real_call_not_simulated(client):
    campaign_id = _setup_running_campaign(client)
    db = _db(client)
    execution = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).first()

    fake_session = type("S", (), {"call_sid": "CAfake123", "session_id": None})()
    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=fake_session)
        await _process_scheduler_tick(db, execution)
        db.commit()
        gw.initiate_call.assert_called_once()

    attempt = db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).first()
    assert attempt is not None
    assert attempt.outcome == "pending"
    assert attempt.session_id is not None
    assert attempt.finished_at is None

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    assert participant.status == "contacted"


@pytest.mark.asyncio
async def test_scheduler_marks_attempt_failed_when_dial_raises(client):
    campaign_id = _setup_running_campaign(client)
    db = _db(client)
    execution = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).first()

    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(side_effect=RuntimeError("Twilio is not configured"))
        await _process_scheduler_tick(db, execution)
        db.commit()

    attempt = db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).first()
    assert attempt.outcome == "failed"
    assert attempt.finished_at is not None
    assert "dial error" in attempt.note

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    assert participant.status == "failed"


def test_status_webhook_finalizes_pending_attempt_as_success(client):
    campaign_id = _setup_running_campaign(client)
    db = _db(client)

    # Simulate a CallAttempt already placed by the scheduler, awaiting outcome.
    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    attempt = CallAttempt(
        campaign_id=campaign_id,
        participant_id=participant.id,
        session_id="sess-webhook-test",
        attempt_number=1,
        outcome="pending",
    )
    db.add(attempt)
    db.commit()

    with patch("app.telephony.router.get_store") as mock_get_store:
        fake_tel_session = type(
            "TS", (), {
                "session_id": "sess-webhook-test",
                "state": __import__("app.telephony.session_store", fromlist=["CallState"]).CallState.COMPLETED,
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
                "campaign_id": campaign_id,
                "call_sid": "CAfake456",
                "participant_phone": "+15550001111",
            }
        )()

        async def _update_state(*args, **kwargs):
            return fake_tel_session

        mock_get_store.return_value.update_state = _update_state
        resp = client.post(
            "/api/telephony/webhook/status",
            data={"CallSid": "CAfake456", "CallStatus": "completed"},
        )
        assert resp.status_code == 200

    db.refresh(attempt)
    assert attempt.outcome == "success"
    assert attempt.finished_at is not None

    db.refresh(participant)
    assert participant.status == "contacted"
