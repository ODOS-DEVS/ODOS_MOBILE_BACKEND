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


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
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
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise credentials_error from exc

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error

    if not user.is_active:
        raise_account_blocked()

    return user


def get_optional_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    if not token:
        return None

    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if subject is None:
            return None
        user_id = uuid.UUID(subject)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError):
        return None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    return user
