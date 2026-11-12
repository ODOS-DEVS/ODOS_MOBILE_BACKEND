"""Sign in with Apple token verification.

The claims checks are what stand between ODOS and a token minted for a
different app — equally real, equally signed by Apple — so they are worth
pinning. Apple's quirks (string `email_verified`, no email after the first
authorization, Hide My Email) get the same treatment.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import apple_auth
from app.core.apple_auth import (
    APPLE_ISSUER,
    apple_email_is_verified,
    is_apple_private_relay,
    verify_apple_identity_token,
)
from app.core.config import settings

BUNDLE_ID = "com.paul.odos"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def apple_configured(monkeypatch, signing_key):
    """Point the verifier at a locally generated key instead of Apple's JWKS."""
    monkeypatch.setattr(settings, "apple_audiences", BUNDLE_ID)

    class _Key:
        key = signing_key.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(apple_auth, "_client", lambda: _Client())


def make_token(signing_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": APPLE_ISSUER,
        "aud": BUNDLE_ID,
        "sub": "001234.abcdef.1234",
        "iat": now,
        "exp": now + 600,
        "email": "shopper@example.com",
        "email_verified": "true",
    }
    claims.update(overrides)
    for key in [k for k, v in claims.items() if v is None]:
        del claims[key]
    return jwt.encode(claims, signing_key, algorithm="RS256")


class TestTokenVerification:
    def test_a_valid_token_is_accepted(self, signing_key):
        payload = verify_apple_identity_token(make_token(signing_key))
        assert payload["sub"] == "001234.abcdef.1234"
        assert payload["email"] == "shopper@example.com"

    def test_a_token_for_another_app_is_refused(self, signing_key):
        """The check that actually matters: Apple signed it, but not for us."""
        with pytest.raises(ValueError, match="audience"):
            verify_apple_identity_token(make_token(signing_key, aud="com.someone.else"))

    def test_a_forged_issuer_is_refused(self, signing_key):
        with pytest.raises(ValueError, match="issuer"):
            verify_apple_identity_token(make_token(signing_key, iss="https://evil.example"))

    def test_an_expired_token_is_refused(self, signing_key):
        now = int(time.time())
        with pytest.raises(ValueError, match="expired"):
            verify_apple_identity_token(make_token(signing_key, iat=now - 7200, exp=now - 3600))

    def test_a_token_signed_by_someone_else_is_refused(self, signing_key):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(ValueError):
            verify_apple_identity_token(make_token(other))

    def test_garbage_is_refused_rather_than_crashing(self):
        with pytest.raises(ValueError):
            verify_apple_identity_token("not-a-jwt")

    def test_unconfigured_backend_refuses_every_token(self, monkeypatch, signing_key):
        """Fails closed: with no audience there is nothing to validate against."""
        monkeypatch.setattr(settings, "apple_audiences", "")
        with pytest.raises(ValueError, match="not configured"):
            verify_apple_identity_token(make_token(signing_key))

    def test_multiple_audiences_are_supported(self, monkeypatch, signing_key):
        """An iOS bundle id and a web Services ID can both be live."""
        monkeypatch.setattr(settings, "apple_audiences", f"com.odos.web,{BUNDLE_ID}")
        assert verify_apple_identity_token(make_token(signing_key))["sub"]


class TestAppleQuirks:
    def test_email_verified_accepts_the_string_apple_actually_sends(self):
        assert apple_email_is_verified({"email_verified": "true"}) is True
        assert apple_email_is_verified({"email_verified": True}) is True

    def test_anything_else_is_not_verified(self):
        for raw in ("false", False, None, "", "TRUE"):
            assert apple_email_is_verified({"email_verified": raw}) is False
        assert apple_email_is_verified({}) is False

    def test_a_returning_user_token_carries_no_email(self, signing_key):
        """Apple sends the email once, ever. The token is still valid."""
        payload = verify_apple_identity_token(make_token(signing_key, email=None))
        assert payload.get("email") is None
        assert payload["sub"]

    def test_hide_my_email_addresses_are_recognised_not_rejected(self):
        assert is_apple_private_relay("abc123@privaterelay.appleid.com") is True
        assert is_apple_private_relay("shopper@example.com") is False
        assert is_apple_private_relay(None) is False
