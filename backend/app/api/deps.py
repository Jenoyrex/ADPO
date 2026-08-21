from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.security import SESSION_COOKIE_NAME, read_session_cookie
from app.db.session import get_db as _get_db
from app.models.user import User

get_db = _get_db


def get_current_user(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> User:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_cookie(cookie) if cookie else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session refers to a deleted user")
    return user
