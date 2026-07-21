from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    hashed_password = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed_password.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def create_access_token(subject: str, *, token_version: int = 0) -> str:
    return create_signed_token(
        subject=subject,
        expires_in_minutes=settings.access_token_expire_minutes,
        extra_claims={
            "typ": "access",
            "tv": int(token_version),
        },
    )


def create_signed_token(
    *,
    subject: str,
    expires_in_minutes: int,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=expires_in_minutes)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": expires_at,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_password_reset_token(subject: str, email: str) -> str:
    return create_signed_token(
        subject=subject,
        expires_in_minutes=settings.password_reset_token_expire_minutes,
        extra_claims={
            "purpose": "password_reset",
            "typ": "password_reset",
            "email": email.lower(),
        },
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )
