"""Tests for SAA-140/141/142: Do-Not-Call list and enforcement."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from auth_helpers import login_admin


def test_dnc_list_starts_empty(client):
    login_admin(client)
    resp = client.get("/api/settings/dnc")
    assert resp.status_code == 200
    assert resp.json() == []


def test_dnc_list_requires_auth(client):
    resp = client.get("/api/settings/dnc")
    assert resp.status_code == 401


def test_add_and_list_dnc_entry(client):
    login_admin(client)
    resp = client.post("/api/settings/dnc", json={"phone_number": "+1 (555) 123-4567", "reason": "opted out"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone_number"] == "+15551234567"
    assert body["already_existed"] is False

    listing = client.get("/api/settings/dnc").json()
    assert len(listing) == 1
    assert listing[0]["reason"] == "opted out"


def test_add_duplicate_dnc_entry_is_idempotent(client):
    login_admin(client)
    client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})
    resp = client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})
    assert resp.json()["already_existed"] is True
    assert len(client.get("/api/settings/dnc").json()) == 1


def test_remove_dnc_entry(client):
    login_admin(client)
    entry = client.post("/api/settings/dnc", json={"phone_number": "+15551234567"}).json()
    resp = client.delete(f"/api/settings/dnc/{entry['id']}")
    assert resp.status_code == 200
    assert client.get("/api/settings/dnc").json() == []


def test_remove_missing_dnc_entry_404(client):
    login_admin(client)
    resp = client.delete("/api/settings/dnc/999999")
    assert resp.status_code == 404


def test_audit_log_records_dnc_changes(client):
    login_admin(client)
    client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})
    resp = client.get("/api/settings/audit", params={"category": "dnc"})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "create"
    assert entries[0]["detail"] == "+15551234567"


def test_initiate_call_blocked_for_dnc_number(client):
    login_admin(client)
    client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})

    with patch("app.telephony.router.get_gateway") as mock_get_gateway:
        resp = client.post(
            "/api/telephony/calls",
            params={"to_number": "+15551234567", "campaign_id": 1, "session_id": "sess-1"},
        )
        assert resp.status_code == 403
        mock_get_gateway.assert_not_called()


def test_initiate_call_allowed_for_non_dnc_number(client):
    with patch("app.telephony.router.get_gateway") as mock_get_gateway, \
         patch("app.telephony.router.save_call_log"):
        session = type(
            "S", (), {"call_sid": "CA1", "session_id": "sess-1", "state": type("St", (), {"value": "initiated"})()}
        )()
        gw = mock_get_gateway.return_value
        gw.initiate_call = AsyncMock(return_value=session)
        resp = client.post(
            "/api/telephony/calls",
            params={"to_number": "+15559999999", "campaign_id": 1, "session_id": "sess-1"},
        )
        assert resp.status_code == 200
