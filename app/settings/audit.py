"""SAA-143: Read access to the persistent settings/consent/DNC audit trail."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.deps import require_role
from ..database import get_db
from ..models import SettingsAuditEntry

router = APIRouter(prefix="/api/settings/audit", tags=["settings"])


@router.get("", dependencies=[Depends(require_role("admin"))])
def list_audit_entries(
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(SettingsAuditEntry)
    if category:
        q = q.filter(SettingsAuditEntry.category == category)
    rows = q.order_by(SettingsAuditEntry.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "category": r.category,
            "action": r.action,
            "actor": r.actor,
            "detail": r.detail,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
