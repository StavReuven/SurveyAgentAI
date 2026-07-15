"""Tests for SAA-131: API Keys + Connection Status settings."""
from __future__ import annotations

from auth_helpers import login_admin


def test_list_providers_defaults_not_configured(client):
    login_admin(client)
    resp = client.get("/api/settings/providers")
    assert resp.status_code == 200
    data = resp.json()
    providers = {p["provider"]: p for p in data}
    assert set(providers) == {"anthropic", "twilio", "stt", "tts"}
    assert providers["stt"]["configured"] is False
    assert providers["stt"]["keys"]["api_key"]["configured"] is False


def test_list_providers_requires_auth(client):
    resp = client.get("/api/settings/providers")
    assert resp.status_code == 401


def test_update_provider_stores_encrypted_and_masks_response(client):
    login_admin(client)
    resp = client.put("/api/settings/providers/anthropic", json={"values": {"api_key": "sk-ant-secret1234"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True

    listing = client.get("/api/settings/providers").json()
    anthropic = next(p for p in listing if p["provider"] == "anthropic")
    assert anthropic["configured"] is True
    masked = anthropic["keys"]["api_key"]["masked_value"]
    assert "1234" in masked
    assert "sk-ant-secret1234" not in masked


def test_update_provider_unknown_provider_404(client):
    login_admin(client)
    resp = client.put("/api/settings/providers/nope", json={"values": {"api_key": "x"}})
    assert resp.status_code == 404


def test_update_provider_unknown_key_400(client):
    login_admin(client)
    resp = client.put("/api/settings/providers/anthropic", json={"values": {"bogus": "x"}})
    assert resp.status_code == 400


def test_health_check_not_configured(client):
    login_admin(client)
    resp = client.post("/api/settings/providers/twilio/health-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_configured"
    assert set(body["missing_keys"]) == {"account_sid", "auth_token", "phone_number"}


def test_health_check_configured_after_update(client):
    login_admin(client)
    client.put(
        "/api/settings/providers/twilio",
        json={"values": {"account_sid": "AC123", "auth_token": "tok123", "phone_number": "+15551234567"}},
    )
    resp = client.post("/api/settings/providers/twilio/health-check")
    assert resp.json()["status"] == "configured"


def test_credentials_are_never_returned_in_plaintext(client):
    login_admin(client)
    client.put("/api/settings/providers/stt", json={"values": {"api_key": "topsecretvalue"}})
    listing = client.get("/api/settings/providers").json()
    raw = str(listing)
    assert "topsecretvalue" not in raw
