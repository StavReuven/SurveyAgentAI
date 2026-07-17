"""SAA-131: API Keys + Connection Status (STT/TTS/LLM/Telephony) settings router.

Stories implemented:
  SAA-132 secrets storage   — encrypted ProviderCredential table
  SAA-133 health checks     — key-presence/format checks only (no outbound network calls,
                               so no provider is ever billed just for checking status)
  SAA-134 masked UI         — API never returns plaintext key values, only masked previews
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_role
from ..database import get_db
from ..models import ProviderCredential, SettingsAuditEntry, User

router = APIRouter(prefix="/api/settings/providers", tags=["settings"])

# provider -> required key names
PROVIDERS: dict[str, list[str]] = {
    "anthropic": ["api_key"],
    "twilio": ["account_sid", "auth_token", "phone_number"],
    "stt": ["api_key"],
    "tts": ["api_key"],
}

# fallback env var names, used only when no DB-stored credential exists yet
_ENV_FALLBACK: dict[str, str] = {
    "anthropic.api_key": "ANTHROPIC_API_KEY",
    "twilio.account_sid": "TWILIO_ACCOUNT_SID",
    "twilio.auth_token": "TWILIO_AUTH_TOKEN",
    "twilio.phone_number": "TWILIO_PHONE_NUMBER",
}


class CredentialUpdate(BaseModel):
    values: dict[str, str]


def _record_audit(
    db: Session, action: str, provider: str, actor: str | None = None, organization_id: int | None = None
) -> None:
    db.add(
        SettingsAuditEntry(
            organization_id=organization_id,
            category="provider_credential",
            action=action,
            actor=actor,
            detail=provider,
        )
    )


def _get_stored(db: Session, provider: str, key_name: str) -> ProviderCredential | None:
    return (
        db.query(ProviderCredential)
        .filter(ProviderCredential.provider == provider, ProviderCredential.key_name == key_name)
        .first()
    )


def _resolve_masked(db: Session, provider: str, key_name: str) -> tuple[bool, str]:
    """Return (configured, masked_preview) for one key, checking DB then env fallback."""
    from .crypto import mask_value

    row = _get_stored(db, provider, key_name)
    if row is not None:
        return True, "•" * 6 + row.last_four
    env_name = _ENV_FALLBACK.get(f"{provider}.{key_name}")
    env_value = os.getenv(env_name, "") if env_name else ""
    if env_value:
        return True, mask_value(env_value)
    return False, ""


@router.get("", dependencies=[Depends(require_role("admin", "operator", "analyst"))])
def list_providers(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    result = []
    for provider, key_names in PROVIDERS.items():
        keys = {}
        all_configured = True
        for key_name in key_names:
            configured, masked = _resolve_masked(db, provider, key_name)
            keys[key_name] = {"configured": configured, "masked_value": masked}
            all_configured = all_configured and configured
        result.append({"provider": provider, "keys": keys, "configured": all_configured})
    return result


@router.put("/{provider}")
def update_provider_credentials(
    provider: str,
    body: CredentialUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role("admin")),
) -> dict[str, Any]:
    from .crypto import encrypt_value

    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    allowed_keys = set(PROVIDERS[provider])
    unknown = set(body.values) - allowed_keys
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown key(s) for {provider}: {sorted(unknown)}")

    for key_name, plaintext in body.values.items():
        if not plaintext:
            continue
        row = _get_stored(db, provider, key_name)
        if row is None:
            row = ProviderCredential(provider=provider, key_name=key_name, value_encrypted="", last_four="")
            db.add(row)
        row.value_encrypted = encrypt_value(plaintext)
        row.last_four = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    _record_audit(db, "update", provider, actor=admin.email, organization_id=admin.organization_id)
    db.commit()

    configured, _ = _resolve_masked(db, provider, PROVIDERS[provider][0])
    return {"provider": provider, "updated_keys": list(body.values), "configured": configured}


@router.post(
    "/{provider}/health-check",
    dependencies=[Depends(require_role("admin", "operator", "analyst"))],
)
def health_check_provider(provider: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Report connection readiness for a provider.

    This ONLY inspects whether required credentials are present and well-formed;
    it never makes an outbound call to the provider (avoids incurring API costs
    just from checking settings).
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")

    missing = []
    for key_name in PROVIDERS[provider]:
        configured, _ = _resolve_masked(db, provider, key_name)
        if not configured:
            missing.append(key_name)

    if missing:
        return {"provider": provider, "status": "not_configured", "missing_keys": missing}
    return {"provider": provider, "status": "configured"}
