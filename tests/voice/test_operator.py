"""SAA-94: Tests for Operator Console — takeover, return, hangup, audit trail."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.voice.escalation import EscalationReason, EscalationSnapshot, get_escalation_queue
from app.operator.audit import AuditLog, OperatorAction, get_audit_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(session_id: str = "sess-1", score: float = 7.0) -> EscalationSnapshot:
    return EscalationSnapshot(
        session_id=session_id,
        campaign_id=1,
        participant_phone="+1234567890",
        reason=EscalationReason.AGENT_REQUESTED,
        triggered_at=datetime(2026, 6, 23, 10, 0, 0),
        urgency_score=score,
        history=[
            {"event": "caller_input", "text": "I want a human"},
            {"event": "bot_response", "text": "Transferring you now."},
        ],
        answers_so_far={"q1": "8"},
    )


@pytest.fixture(autouse=True)
def clean_queue_and_audit():
    """Reset global state before each test."""
    q = get_escalation_queue()
    q._index.clear()
    q._heap.clear()
    get_audit_log()._entries.clear()
    yield


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# SAA-89: Queue API
# ---------------------------------------------------------------------------

class TestQueueAPI:
    def test_empty_queue(self, client):
        r = client.get("/api/operator/queue")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_queue_returns_sorted_sessions(self, client):
        q = get_escalation_queue()
        q.push(_snap("low",  score=2.0))
        q.push(_snap("high", score=9.0))
        r = client.get("/api/operator/queue")
        data = r.json()
        assert data["count"] == 2
        assert data["sessions"][0]["session_id"] == "high"
        assert data["sessions"][1]["session_id"] == "low"

    def test_get_single_session(self, client):
        get_escalation_queue().push(_snap("sess-x", score=5.0))
        r = client.get("/api/operator/queue/sess-x")
        assert r.status_code == 200
        assert r.json()["session_id"] == "sess-x"

    def test_get_session_not_found(self, client):
        r = client.get("/api/operator/queue/nonexistent")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# SAA-97: Control actions
# ---------------------------------------------------------------------------

class TestTakeover:
    def test_takeover_success(self, client):
        get_escalation_queue().push(_snap())
        r = client.post("/api/operator/sessions/sess-1/takeover",
                        json={"operator_id": "op-1"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "taken_over"
        assert data["session"]["operator_id"] == "op-1"

    def test_takeover_not_found(self, client):
        r = client.post("/api/operator/sessions/ghost/takeover",
                        json={"operator_id": "op-1"})
        assert r.status_code == 404

    def test_double_takeover_rejected(self, client):
        get_escalation_queue().push(_snap())
        client.post("/api/operator/sessions/sess-1/takeover", json={"operator_id": "op-1"})
        r = client.post("/api/operator/sessions/sess-1/takeover", json={"operator_id": "op-2"})
        assert r.status_code == 409

    def test_takeover_logged_in_audit(self, client):
        get_escalation_queue().push(_snap())
        client.post("/api/operator/sessions/sess-1/takeover", json={"operator_id": "op-1"})
        entries = get_audit_log().get_for_session("sess-1")
        assert len(entries) == 1
        assert entries[0].action == OperatorAction.TAKEOVER
        assert entries[0].operator_id == "op-1"


class TestReturnToAgent:
    def test_return_removes_from_queue(self, client):
        get_escalation_queue().push(_snap())
        client.post("/api/operator/sessions/sess-1/takeover", json={"operator_id": "op-1"})
        r = client.post("/api/operator/sessions/sess-1/return", json={"operator_id": "op-1"})
        assert r.status_code == 200
        assert r.json()["status"] == "returned_to_agent"
        assert get_escalation_queue().get("sess-1") is None

    def test_return_logged_in_audit(self, client):
        get_escalation_queue().push(_snap())
        client.post("/api/operator/sessions/sess-1/return", json={"operator_id": "op-1"})
        entries = get_audit_log().get_for_session("sess-1")
        assert any(e.action == OperatorAction.RETURN_TO_AGENT for e in entries)


class TestHangup:
    def test_hangup_removes_from_queue(self, client):
        get_escalation_queue().push(_snap())
        r = client.post("/api/operator/sessions/sess-1/hangup", json={"operator_id": "op-1"})
        assert r.status_code == 200
        assert get_escalation_queue().get("sess-1") is None

    def test_hangup_logged_in_audit(self, client):
        get_escalation_queue().push(_snap())
        client.post("/api/operator/sessions/sess-1/hangup", json={"operator_id": "op-1"})
        entries = get_audit_log().get_for_session("sess-1")
        assert any(e.action == OperatorAction.HANGUP for e in entries)


# ---------------------------------------------------------------------------
# SAA-96: Transcript view
# ---------------------------------------------------------------------------

class TestTranscript:
    def test_transcript_returns_history(self, client):
        get_escalation_queue().push(_snap())
        r = client.get("/api/operator/sessions/sess-1/transcript")
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "sess-1"
        assert len(data["transcript"]) == 2
        assert data["answers_so_far"] == {"q1": "8"}

    def test_transcript_not_found(self, client):
        r = client.get("/api/operator/sessions/ghost/transcript")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# SAA-98: Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_all(self, client):
        get_escalation_queue().push(_snap("s1"))
        get_escalation_queue().push(_snap("s2", score=5.0))
        client.post("/api/operator/sessions/s1/takeover", json={"operator_id": "op-A"})
        client.post("/api/operator/sessions/s2/hangup",   json={"operator_id": "op-B"})
        r = client.get("/api/operator/audit")
        entries = r.json()["entries"]
        assert len(entries) == 2

    def test_audit_filter_by_session(self, client):
        get_escalation_queue().push(_snap("s1"))
        get_escalation_queue().push(_snap("s2", score=5.0))
        client.post("/api/operator/sessions/s1/takeover", json={"operator_id": "op-A"})
        client.post("/api/operator/sessions/s2/hangup",   json={"operator_id": "op-B"})
        r = client.get("/api/operator/audit?session_id=s1")
        entries = r.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["session_id"] == "s1"

    def test_audit_log_unit(self):
        log = AuditLog()
        log.record("s1", "op-1", OperatorAction.TAKEOVER)
        log.record("s2", "op-2", OperatorAction.HANGUP)
        assert len(log.get_all()) == 2
        assert len(log.get_for_session("s1")) == 1
