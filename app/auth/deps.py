"""SAA-137/138: Auth dependencies — current-user resolution and role guards."""
from __future__ import annotations

from datetime import datetime

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Session_ as SessionModel
from ..models import User

SESSION_COOKIE_NAME = "sa_session"


def get_current_user(
    sa_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    if not sa_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    row = (
        db.query(SessionModel, User)
        .join(User, User.id == SessionModel.user_id)
        .filter(SessionModel.token == sa_session, User.is_active.is_(True))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    session_row, user = row
    if session_row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


def require_role(*roles: str):
    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _dependency
