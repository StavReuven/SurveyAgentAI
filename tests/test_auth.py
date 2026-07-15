"""Tests for SAA-136/137/138: auth middleware and role permissions (RBAC)."""
from __future__ import annotations

from auth_helpers import create_and_login, login_admin


def test_login_bootstraps_default_admin(client):
    body = login_admin(client)
    assert body["role"] == "admin"
    assert body["email"] == "admin@example.com"


def test_login_wrong_password_401(client):
    login_admin(client)  # bootstrap
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_session(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    login_admin(client)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_logout_invalidates_session(client):
    login_admin(client)
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_admin_can_create_users_other_roles_cannot(client):
    login_admin(client)
    resp = client.post(
        "/api/auth/users", json={"email": "op@example.com", "password": "password123", "role": "operator"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"

    create_and_login(client, "analyst@example.com", "analyst")
    resp = client.post(
        "/api/auth/users", json={"email": "another@example.com", "password": "password123", "role": "analyst"}
    )
    assert resp.status_code == 403


def test_analyst_cannot_write_provider_credentials(client):
    create_and_login(client, "analyst@example.com", "analyst")
    resp = client.put("/api/settings/providers/anthropic", json={"values": {"api_key": "x"}})
    assert resp.status_code == 403


def test_analyst_can_read_provider_status(client):
    create_and_login(client, "analyst@example.com", "analyst")
    resp = client.get("/api/settings/providers")
    assert resp.status_code == 200


def test_operator_can_manage_dnc_but_not_read_audit(client):
    create_and_login(client, "op@example.com", "operator")
    resp = client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})
    assert resp.status_code == 200

    resp = client.get("/api/settings/audit")
    assert resp.status_code == 403


def test_analyst_cannot_manage_dnc(client):
    create_and_login(client, "analyst2@example.com", "analyst")
    resp = client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})
    assert resp.status_code == 403


def test_deactivated_user_cannot_authenticate(client):
    login_admin(client)
    created = client.post(
        "/api/auth/users", json={"email": "temp@example.com", "password": "password123", "role": "analyst"}
    ).json()

    login_resp = client.post("/api/auth/login", json={"email": "temp@example.com", "password": "password123"})
    assert login_resp.status_code == 200

    login_admin(client)
    resp = client.delete(f"/api/auth/users/{created['id']}")
    assert resp.status_code == 200

    login_resp = client.post("/api/auth/login", json={"email": "temp@example.com", "password": "password123"})
    assert login_resp.status_code == 401
