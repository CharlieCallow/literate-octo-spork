"""Single-password basic auth. The hash lives in env (DASHBOARD_PASSWORD_HASH)."""

from __future__ import annotations

import secrets

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from api.settings import settings

_security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    if not settings.dashboard_password_hash:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "DASHBOARD_PASSWORD_HASH not configured",
        )
    expected_user = "admin"
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pw_ok = bcrypt.checkpw(
        credentials.password.encode("utf-8"),
        settings.dashboard_password_hash.encode("utf-8"),
    )
    if not (user_ok and pw_ok):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
