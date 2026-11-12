"""Verify Sign in with Apple identity tokens.

Apple does not ship a verification helper the way Google does, so this does
the JWT work directly: fetch Apple's public keys, check the signature, and
hold the issuer and audience to what we expect. `PyJWKClient` caches the key
set in memory, so the keys are fetched once rather than per sign-in.
"""

from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.config import settings

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"

#: Module-level so the key set is cached across requests. Apple rotates keys
#: rarely, and PyJWKClient refetches on an unknown `kid` by itself.
_jwk_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(APPLE_JWKS_URL, cache_keys=True)
    return _jwk_client


def verify_apple_identity_token(token: str) -> dict[str, Any]:
    """Return the verified claims, or raise ValueError with a usable reason.

    The audience is the app's bundle id (or the Services ID for web). Checking
    it is what stops a token minted for an unrelated app — equally valid, and
    equally signed by Apple — from being accepted as one of ours.
    """
    audiences = settings.apple_audience_list
    if not audiences:
        raise ValueError("Apple sign-in is not configured on the backend.")

    try:
        signing_key = _client().get_signing_key_from_jwt(token)
    except Exception as exc:  # network, malformed token, unknown key id
        raise ValueError("Could not verify that Apple token.") from exc

    try:
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences,
            issuer=APPLE_ISSUER,
            # Apple always sends these; requiring them means a token missing
            # one is rejected here rather than read as an absent claim later.
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("That Apple sign-in expired. Please try again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise ValueError("Apple token audience does not match this app.") from exc
    except jwt.InvalidIssuerError as exc:
        raise ValueError("Invalid Apple token issuer.") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("Invalid Apple token.") from exc

    if not payload.get("sub"):
        raise ValueError("Apple token is missing its subject.")

    return payload


def apple_email_is_verified(payload: dict[str, Any]) -> bool:
    """Apple sends `email_verified` as a bool on some paths and the string
    "true" on others; treat both as verified and anything else as not."""
    raw = payload.get("email_verified")
    return raw is True or raw == "true"


def is_apple_private_relay(email: str | None) -> bool:
    """Apple's "Hide My Email" addresses. They forward real mail, so they are
    perfectly usable — worth knowing about, never worth rejecting."""
    return bool(email) and email.strip().lower().endswith("@privaterelay.appleid.com")
