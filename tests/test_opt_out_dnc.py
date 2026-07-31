"""Regression tests: a caller who says "don't call me again" (English or
Hebrew) during a live call must actually end up on the Do-Not-Call list —
not just hear a spoken promise. Previously AgentIntent.OPT_OUT was detected
and spoken aloud but never written anywhere (see AgentDecision.to_nlu_intent,
which collapses it into the same handling as a plain "call me later"), so the
caller was silently re-dialed on the next scheduler tick regardless."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from auth_helpers import login_admin

from app.database import get_db
from app.main import app, _process_scheduler_tick
from app.models import CampaignExecution, DoNotCallEntry, Participant


def _db(client):
    return next(app.dependency_overrides[get_db]())


def _dial_one_participant(client, phone: str) -> tuple[int, str]:
    """Create a running campaign with one participant and place a (mocked)
    real outbound call for them, exactly like the scheduler would — returns
    (campaign_id, session_id) with the CallLog properly linked to a
    Participant row, which is what makes DNC/opt_in enforcement meaningful."""
    login_admin(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Opt Out Test", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign_id = campaign["id"]
    client.post(
        f"/api/campaigns/{campaign_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(
        f"/api/campaigns/{campaign_id}/participants/upload",
        files={"file": ("p.csv", f"phone_number,full_name,locale\n{phone},Test,en-US\n", "text/csv")},
    )
    client.post(f"/api/campaigns/{campaign_id}/start")

    db = _db(client)
    execution = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign_id).first()
    fake_session = type("S", (), {"call_sid": "CAfake", "session_id": None})()
    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=fake_session)
        asyncio.run(_process_scheduler_tick(db, execution))
        db.commit()

    from app.models import CallAttempt
    attempt = db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign_id).first()
    return campaign_id, attempt.session_id


def test_opt_out_creates_dnc_entry(client):
    phone = "+15557778001"
    campaign_id, session_id = _dial_one_participant(client, phone)

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "stop calling me, remove me from your list", "audio_duration_ms": 800},
    )
    assert resp.status_code == 200
    assert resp.json()["session_complete"] is True

    db = _db(client)
    entry = db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).first()
    assert entry is not None
    assert entry.added_by == "voice-agent"

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    assert participant.opt_in is False


def test_hebrew_opt_out_creates_dnc_entry(client):
    phone = "+15557778002"
    campaign_id, session_id = _dial_one_participant(client, phone)

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "אל תתקשרו אליי יותר", "audio_duration_ms": 800},
    )
    assert resp.status_code == 200

    db = _db(client)
    entry = db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).first()
    assert entry is not None


def test_opt_out_blocks_subsequent_manual_dial(client):
    phone = "+15557778003"
    campaign_id, session_id = _dial_one_participant(client, phone)

    client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "do not call me again", "audio_duration_ms": 800},
    )

    with patch("app.telephony.router.get_gateway") as mock_get_gateway:
        resp = client.post(
            "/api/telephony/calls",
            params={"to_number": phone, "campaign_id": campaign_id, "session_id": "sess-new"},
        )
        assert resp.status_code == 403
        mock_get_gateway.assert_not_called()


def test_plain_not_now_does_not_create_dnc_entry(client):
    """Regression guard: an ordinary "call me later" must NOT get treated as
    a permanent opt-out — only the distinct OPT_OUT intent should."""
    phone = "+15557778004"
    campaign_id, session_id = _dial_one_participant(client, phone)

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "not now, call me back later", "audio_duration_ms": 800},
    )
    assert resp.status_code == 200

    db = _db(client)
    entry = db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).first()
    assert entry is None

    participant = db.query(Participant).filter(Participant.campaign_id == campaign_id).first()
    assert participant.opt_in is True


def test_opt_out_priority_over_profanity_and_escalate(client):
    """Regression guard: the pre-existing priority order (profanity/escalate
    checked before opt-out in fallback.analyze) must survive the new Hebrew
    opt-out patterns — a caller asking for a manager must still be escalated,
    not silently opted out, even if their sentence also contains an opt-out
    phrase."""
    phone = "+15557778005"
    campaign_id, session_id = _dial_one_participant(client, phone)

    resp = client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "get me a manager and stop calling me", "audio_duration_ms": 800},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dialogue_action"] == "escalate"

    db = _db(client)
    entry = db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).first()
    assert entry is None, "escalation must win over opt-out — no DNC entry should be written"


def test_opt_out_blocks_dial_from_a_different_campaign_too(client):
    """DNC is phone-number-scoped, not campaign-scoped: once a number opts
    out via one campaign, a completely different campaign's scheduler must
    never dial it either — proving the block isn't accidentally local to
    the campaign the opt-out happened on."""
    phone = "+15557778006"
    campaign_id, session_id = _dial_one_participant(client, phone)
    client.post(
        f"/api/campaigns/{campaign_id}/voice/sessions/{session_id}/turn",
        json={"transcript": "do not call me again", "audio_duration_ms": 800},
    )

    db = _db(client)
    assert db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).count() == 1

    # A second, unrelated campaign uploads the SAME phone number as a fresh
    # participant and ticks its own scheduler — is_blocked() must stop it
    # from ever placing the call in the first place.
    login_admin(client)
    campaign2 = client.post(
        "/api/campaigns",
        json={"name": "Second Campaign", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()
    campaign2_id = campaign2["id"]
    client.post(
        f"/api/campaigns/{campaign2_id}/questions",
        json={"key": "q1", "prompt": "How are you?", "question_type": "free_text", "required": True, "config": {}},
    )
    client.post(
        f"/api/campaigns/{campaign2_id}/participants/upload",
        files={"file": ("p.csv", f"phone_number,full_name,locale\n{phone},Test,en-US\n", "text/csv")},
    )
    client.post(f"/api/campaigns/{campaign2_id}/start")

    execution2 = db.query(CampaignExecution).filter(CampaignExecution.campaign_id == campaign2_id).first()
    with patch("app.main.get_gateway") as mock_get_gateway:
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=type("S", (), {"call_sid": "CA2", "session_id": None})())
        asyncio.run(_process_scheduler_tick(db, execution2))
        db.commit()
        gw.initiate_call.assert_not_called()

    from app.models import CallAttempt
    assert db.query(CallAttempt).filter(CallAttempt.campaign_id == campaign2_id).count() == 0
    assert db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == phone).count() == 1
