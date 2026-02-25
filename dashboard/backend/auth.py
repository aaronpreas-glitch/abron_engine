"""
auth.py — Single-user JWT authentication for the dashboard.
Password stored in DASHBOARD_PASSWORD env var.
Secret key in DASHBOARD_SECRET_KEY env var.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "change-me-please-use-a-real-secret")
_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 12
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

_bearer = HTTPBearer(auto_error=False)


def _make_token() -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": "dashboard", "exp": exp}, _SECRET_KEY, algorithm=_ALGORITHM)


def verify_password(password: str) -> bool:
    if not _DASHBOARD_PASSWORD:
        return False
    return hmac.compare_digest(password.encode(), _DASHBOARD_PASSWORD.encode())


def create_access_token() -> str:
    return _make_token()


def _decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        if payload.get("sub") != "dashboard":
            raise ValueError("invalid subject")
        return payload
    except JWTError as exc:
        raise ValueError(str(exc)) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """FastAPI dependency — raises 401 if token is missing or invalid."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        _decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "dashboard"


def validate_ws_token(token: str) -> bool:
    """Used by WebSocket endpoint to validate first-frame auth token."""
    try:
        _decode_token(token)
        return True
    except ValueError:
        return False
