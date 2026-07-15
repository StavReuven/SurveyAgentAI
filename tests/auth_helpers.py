"""Shared login helpers for tests exercising RBAC-guarded endpoints."""
from __future__ import annotations

DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "changeme123"


def login_admin(client, email=DEFAULT_ADMIN_EMAIL, password=DEFAULT_ADMIN_PASSWORD):
    """Log in as the bootstrap admin (created lazily on first login attempt)."""
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def create_and_login(client, email: str, role: str, password: str = "password123"):
    """As admin, create a user with the given role, then log in as that user."""
    login_admin(client)
    resp = client.post("/api/auth/users", json={"email": email, "password": password, "role": role})
    assert resp.status_code == 200, resp.text
    login_resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    return login_resp.json()
