"""KutanaPay service: signature verification, status mapping, amount handling.

These three are where a payment integration goes quietly wrong, so they are
tested directly rather than through the controller. The signature tests matter
most: KutanaPay's webhook is the first collections callback ODOS can actually
trust, and that trust rests entirely on the HMAC being compared correctly.
"""

import hashlib
import hmac

import pytest

from app.core.config import settings
from app.services import kutanapay_service as kp

SECRET = "whsec_test_abc123"


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "kutanapay_webhook_secret", SECRET)
    return SECRET


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- signature verification -------------------------------------------------

def test_valid_signature_passes(webhook_secret):
    body = b'{"event_type":"checkout.paid","data":{"checkout_id":"abc"}}'
    assert kp.verify_webhook_signature(body, sign(body)) is True


def test_tampered_body_fails(webhook_secret):
    body = b'{"amount":150}'
    signature = sign(body)
    assert kp.verify_webhook_signature(b'{"amount":15000}', signature) is False


def test_signature_from_a_different_secret_fails(webhook_secret):
    body = b'{"event_type":"checkout.paid"}'
    assert kp.verify_webhook_signature(body, sign(body, "whsec_someone_else")) is False


def test_missing_signature_header_fails(webhook_secret):
    assert kp.verify_webhook_signature(b"{}", None) is False


def test_signature_without_the_sha256_prefix_fails(webhook_secret):
    """The header is documented as `sha256=<hex>`; a bare hex digest is not it."""
    body = b"{}"
    bare = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert kp.verify_webhook_signature(body, bare) is False


def test_unconfigured_secret_rejects_everything(monkeypatch):
    """Without a secret nothing can be verified, so nothing is accepted.

    Failing closed matters here: the alternative is treating every unsigned
    request as genuine the moment the secret goes missing from the environment.
    """
    monkeypatch.setattr(settings, "kutanapay_webhook_secret", "")
    body = b"{}"
    assert kp.verify_webhook_signature(body, sign(body)) is False


def test_byte_for_byte_body_is_required(webhook_secret):
    """Re-serialised JSON must not verify.

    This is the mistake the route is written to avoid -- json.dumps of a parsed
    body changes separators, so the HMAC differs even though the data matches.
    """
    original = b'{"a": 1, "b": 2}'
    reserialised = b'{"a":1,"b":2}'
    assert kp.verify_webhook_signature(reserialised, sign(original)) is False


# --- status mapping ---------------------------------------------------------

@pytest.mark.parametrize("raw", ["paid", "completed", "approved", "PAID", " Paid "])
def test_paid_statuses(raw):
    assert kp.normalize_status(raw) == "paid"


@pytest.mark.parametrize("raw", ["failed", "rejected", "cancelled", "expired", "timeout"])
def test_failed_statuses(raw):
    assert kp.normalize_status(raw) == "failed"


@pytest.mark.parametrize("raw", ["pending", "processing", "created"])
def test_pending_statuses(raw):
    assert kp.normalize_status(raw) == "pending"


@pytest.mark.parametrize("raw", [None, "", "something_new", 42])
def test_unknown_status_is_pending_not_failed(raw):
    """An unrecognised status is a reason to ask again, not to tell a customer
    their payment failed. KutanaPay's own docs say the exact set varies by rail.
    """
    assert kp.normalize_status(raw) == "pending"


# --- amount conversion ------------------------------------------------------

def test_major_units_convert_to_pesewas():
    assert kp.parse_amount_to_subunit(150) == 15000
    assert kp.parse_amount_to_subunit("99.99") == 9999


@pytest.mark.parametrize("amount,expected", [(0.29, 29), (0.57, 57), (1.13, 113), (1.15, 115)])
def test_float_representation_does_not_lose_a_pesewa(amount, expected):
    """Rounding, not truncation.

    0.29 * 100 is 28.999999999999996 in IEEE 754, so int() would record 28 --
    under-counting a real payment by a pesewa and failing the amount check on a
    perfectly good transaction. These four are actual truncating values, found
    by scanning every amount from 0.01 to 999.99 rather than assumed.
    """
    assert kp.parse_amount_to_subunit(amount) == expected
    assert int(amount * 100) == expected - 1  # what truncation would have done


def test_unparseable_amount_is_none_not_zero():
    """None forces the caller to reject; 0 would quietly pass an amount check
    against a free order."""
    assert kp.parse_amount_to_subunit(None) is None
    assert kp.parse_amount_to_subunit("abc") is None
    assert kp.parse_amount_to_subunit({}) is None


def test_zero_is_zero_not_none():
    assert kp.parse_amount_to_subunit(0) == 0


# --- references -------------------------------------------------------------

def test_reference_is_unique_and_prefixed():
    a, b = kp.generate_reference(), kp.generate_reference()
    assert a != b
    assert a.startswith("odos-")


def test_unconfigured_gateway_refuses_rather_than_calling_out(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "kutanapay_api_key", "")
    with pytest.raises(HTTPException) as exc:
        kp.ensure_kutanapay_configured()
    assert exc.value.status_code == 503
