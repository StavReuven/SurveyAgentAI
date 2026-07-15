"""SAA-140/141: Do-Not-Call list — storage, lookup, and API.

SAA-142 (enforcement) imports `is_blocked` from here and calls it before dialing.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_role
from ..database import get_db
from ..models import DoNotCallEntry, SettingsAuditEntry

router = APIRouter(prefix="/api/settings/dnc", tags=["settings"])


def normalize_phone(phone_number: str) -> str:
    """Strip everything but a leading '+' and digits, for consistent lookups."""
    digits = re.sub(r"[^\d+]", "", phone_number or "")
    return digits


def is_blocked(db: Session, phone_number: str) -> bool:
    normalized = normalize_phone(phone_number)
    if not normalized:
        return False
    return (
        db.query(DoNotCallEntry)
        .filter(DoNotCallEntry.phone_number == normalized)
        .first()
        is not None
    )


class DncCreate(BaseModel):
    phone_number: str
    reason: str | None = None
    added_by: str | None = None


@router.get("", dependencies=[Depends(require_role("admin", "operator", "analyst"))])
def list_dnc(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(DoNotCallEntry).order_by(DoNotCallEntry.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "phone_number": r.phone_number,
            "reason": r.reason,
            "added_by": r.added_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("", dependencies=[Depends(require_role("admin", "operator"))])
def add_dnc(body: DncCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    normalized = normalize_phone(body.phone_number)
    if not normalized:
        raise HTTPException(status_code=400, detail="phone_number is required")
    existing = db.query(DoNotCallEntry).filter(DoNotCallEntry.phone_number == normalized).first()
    if existing is not None:
        return {"id": existing.id, "phone_number": existing.phone_number, "already_existed": True}

    entry = DoNotCallEntry(phone_number=normalized, reason=body.reason, added_by=body.added_by)
    db.add(entry)
    db.add(SettingsAuditEntry(category="dnc", action="create", actor=body.added_by, detail=normalized))
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "phone_number": entry.phone_number, "already_existed": False}


@router.delete("/{entry_id}", dependencies=[Depends(require_role("admin", "operator"))])
def remove_dnc(entry_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    entry = db.query(DoNotCallEntry).filter(DoNotCallEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    phone = entry.phone_number
    db.delete(entry)
    db.add(SettingsAuditEntry(category="dnc", action="delete", actor=None, detail=phone))
    db.commit()
    return {"deleted": True, "phone_number": phone}
