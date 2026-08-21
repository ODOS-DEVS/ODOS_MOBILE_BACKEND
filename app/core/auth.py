import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
ACCOUNT_BLOCKED_ERROR_CODE = "ACCOUNT_BLOCKED"
ACCOUNT_BLOCKED_MESSAGE = "This account has been blocked. Contact ODOS support."


def raise_account_blocked() -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ACCOUNT_BLOCKED_MESSAGE,
        headers={"X-Error-Code": ACCOUNT_BLOCKED_ERROR_CODE},
    )


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def assert_session_token_payload(payload: dict) -> None:
    """Reject password-reset (and other non-session) JWTs as Bearer credentials."""
    purpose = payload.get("purpose")
    token_type = payload.get("typ")
    if purpose or (token_type and token_type != "access"):
        raise _credentials_error()


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    credentials_error = _credentials_error()

    try:
        payload = decode_access_token(token)
        assert_session_token_payload(payload)
        subject = payload.get("sub")
        if subject is None:
            raise credentials_error

        user_id = uuid.UUID(subject)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise credentials_error from exc

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error

    if not user.is_active:
        raise_account_blocked()

    token_version = int(payload.get("tv") or 0)
    if int(getattr(user, "token_version", 0) or 0) != token_version:
        raise credentials_error

    return user


def get_optional_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    if not token:
        return None

    try:
        payload = decode_access_token(token)
        assert_session_token_payload(payload)
        subject = payload.get("sub")
        if subject is None:
            return None
        user_id = uuid.UUID(subject)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError, HTTPException):
        return None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    token_version = int(payload.get("tv") or 0)
    if int(getattr(user, "token_version", 0) or 0) != token_version:
        return None

    return user


def require_admin(user: User) -> None:
    """Verify user is admin; raise HTTPException if not."""
    from app.models.user import UserRole

    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
