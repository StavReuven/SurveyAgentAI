"""Tests for company signup (Organization) and per-organization campaign isolation."""
from __future__ import annotations

from auth_helpers import login_admin


def signup(client, company_name, email, password="password123"):
    resp = client.post(
        "/api/auth/signup",
        json={"company_name": company_name, "email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_signup_creates_org_and_logs_in(client):
    body = signup(client, "Acme Corp", "founder@acme.com")
    assert body["role"] == "admin"
    assert body["organization"] == "Acme Corp"

    me = client.get("/api/auth/me").json()
    assert me["organization"] == "Acme Corp"


def test_signup_duplicate_email_rejected(client):
    signup(client, "Acme Corp", "founder@acme.com")
    resp = client.post(
        "/api/auth/signup",
        json={"company_name": "Other Co", "email": "founder@acme.com", "password": "password123"},
    )
    assert resp.status_code == 400


def test_campaigns_are_isolated_per_organization(client):
    signup(client, "Acme Corp", "acme@example.com")
    created = client.post(
        "/api/campaigns",
        json={"name": "Acme Survey", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    ).json()

    client.post("/api/auth/logout")
    signup(client, "Globex Inc", "globex@example.com")

    listing = client.get("/api/campaigns").json()
    assert listing == []

    resp = client.get(f"/api/campaigns/{created['id']}")
    assert resp.status_code == 404


def test_campaign_endpoints_require_auth(client):
    resp = client.get("/api/campaigns")
    assert resp.status_code == 401
    resp = client.post(
        "/api/campaigns",
        json={"name": "x", "language": "en", "timezone": "UTC", "consent_text": "c"},
    )
    assert resp.status_code == 401


def test_users_list_scoped_to_own_organization(client):
    signup(client, "Acme Corp", "acme-admin@example.com")
    client.post(
        "/api/auth/users",
        json={"email": "acme-analyst@example.com", "password": "password123", "role": "analyst"},
    )

    client.post("/api/auth/logout")
    signup(client, "Globex Inc", "globex-admin@example.com")

    users = client.get("/api/auth/users").json()
    emails = {u["email"] for u in users}
    assert emails == {"globex-admin@example.com"}


def test_dashboard_and_analytics_show_nothing_for_brand_new_org(client):
    signup(client, "Acme Corp", "acme@example.com")
    client.post(
        "/api/campaigns",
        json={"name": "Acme Survey", "language": "en", "timezone": "UTC", "consent_text": "consent"},
    )

    client.post("/api/auth/logout")
    signup(client, "Globex Inc", "globex@example.com")

    kpis = client.get("/api/dashboard/kpis").json()
    assert kpis["total_calls"] == 0
    assert kpis["active_campaigns"] == 0

    overview = client.get("/api/analytics/overview").json()
    assert overview["total_calls"] == 0

    dropdown = client.get("/api/dashboard/campaigns").json()
    assert dropdown == []


def test_audit_log_scoped_to_own_organization(client):
    signup(client, "Acme Corp", "acme-admin2@example.com")
    client.post("/api/settings/dnc", json={"phone_number": "+15551234567"})

    client.post("/api/auth/logout")
    signup(client, "Globex Inc", "globex-admin2@example.com")

    entries = client.get("/api/settings/audit").json()
    assert entries == []
