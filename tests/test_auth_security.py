"""Auth security tests: session tokens, reset-token rejection, token versioning."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import assert_session_token_payload, get_current_user
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    decode_access_token,
)


def test_assert_session_token_rejects_password_reset_purpose():
    token = create_password_reset_token(subject=str(uuid.uuid4()), email="a@odos.test")
    payload = decode_access_token(token)
    with pytest.raises(HTTPException) as exc:
        assert_session_token_payload(payload)
    assert exc.value.status_code == 401


def test_access_token_includes_typ_and_token_version():
    token = create_access_token(subject=str(uuid.uuid4()), token_version=3)
    payload = decode_access_token(token)
    assert payload.get("typ") == "access"
    assert payload.get("tv") == 3
    assert_session_token_payload(payload)


def test_get_current_user_rejects_reset_token(monkeypatch):
    user_id = uuid.uuid4()
    reset_token = create_password_reset_token(subject=str(user_id), email="a@odos.test")

    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        get_current_user(token=reset_token, db=db)
    assert exc.value.status_code == 401
    db.get.assert_not_called()


def test_get_current_user_rejects_stale_token_version():
    user_id = uuid.uuid4()
    access = create_access_token(subject=str(user_id), token_version=1)

    user = MagicMock()
    user.is_active = True
    user.token_version = 2

    db = MagicMock()
    db.get.return_value = user

    with pytest.raises(HTTPException) as exc:
        get_current_user(token=access, db=db)
    assert exc.value.status_code == 401


def test_get_current_user_accepts_matching_token_version():
    user_id = uuid.uuid4()
    access = create_access_token(subject=str(user_id), token_version=4)

    user = MagicMock()
    user.is_active = True
    user.token_version = 4

    db = MagicMock()
    db.get.return_value = user

    result = get_current_user(token=access, db=db)
    assert result is user


def test_expired_access_token_raises_401():
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
        "typ": "access",
        "tv": 0,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, db=db)
    assert exc.value.status_code == 401
    assert "expired" in str(exc.value.detail).lower()
