"""SAA-136/137/138: Login/logout, current-user, and user management (RBAC)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Session_ as SessionModel
from ..models import SettingsAuditEntry, User
from .deps import SESSION_COOKIE_NAME, get_current_user, require_role
from .security import generate_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_TTL_HOURS = 12
ROLES = ("admin", "operator", "analyst")


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: str = "analyst"


def _bootstrap_admin_if_needed(db: Session) -> None:
    """Create a default admin user on first-ever login attempt so the app is never lockable."""
    if db.query(User).count() > 0:
        return
    email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    password = os.getenv("ADMIN_PASSWORD", "changeme123")
    db.add(User(email=email, password_hash=hash_password(password), role="admin"))
    db.commit()


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    _bootstrap_admin_if_needed(db)

    user = db.query(User).filter(User.email == body.email, User.is_active.is_(True)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = generate_token()
    db.add(
        SessionModel(
            token=token,
            user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
        )
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        max_age=SESSION_TTL_HOURS * 3600,
        samesite="lax",
    )
    return {"email": user.email, "role": user.role}


@router.post("/logout")
def logout(
    response: Response,
    sa_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if sa_session:
        db.query(SessionModel).filter(SessionModel.token == sa_session).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"logged_out": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"email": user.email, "role": user.role}


@router.get("/users")
def list_users(
    _: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.query(User).order_by(User.created_at.asc()).all()
    return [
        {"id": r.id, "email": r.email, "role": r.role, "is_active": r.is_active}
        for r in rows
    ]


@router.post("/users")
def create_user(
    body: UserCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    if db.query(User).filter(User.email == body.email).first() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(email=body.email, password_hash=hash_password(body.password), role=body.role)
    db.add(new_user)
    db.add(SettingsAuditEntry(category="rbac", action="create", actor=admin.email, detail=f"{body.email}:{body.role}"))
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "email": new_user.email, "role": new_user.role}


@router.delete("/users/{user_id}")
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.is_active = False
    db.add(SettingsAuditEntry(category="rbac", action="delete", actor=admin.email, detail=target.email))
    db.commit()
    return {"deactivated": True, "email": target.email}
