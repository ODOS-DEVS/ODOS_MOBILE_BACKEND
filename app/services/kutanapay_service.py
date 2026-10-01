"""KutanaPay hosted checkout.

Replaces iPay as the collections gateway. Three differences from iPay shape the
code below, and all three are improvements:

**There is a JSON initiate.** ``POST /api/v1/merchants/checkouts`` answers with
a ``checkout_url``, so the app can be handed a plain URL. iPay had no such
endpoint -- it took an HTML form POST and answered with a page, which is why the
old integration had to render a self-submitting form.

**The webhook is signed.** ``X-Webhook-Signature: sha256=<hex>`` is an
HMAC-SHA256 of the raw request body. iPay's IPN carried no signature at all, so
it could only ever be a hint to go and ask. Here the callback can be trusted --
though the amount is still re-checked against the order before anything is
released, because a signature proves origin, not correctness.

**Amounts are in major units.** KutanaPay quotes ``150.00`` GHS where Paystack
counts pesewas and iPay counted subunits. ``Order.total_amount`` is already GHS,
so it is sent unchanged; what comes back is converted to subunits for the ledger
comparison. Getting this backwards charges a customer 100x or 1/100x, so the two
directions are kept in named helpers rather than inline arithmetic.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

import requests
from fastapi import HTTPException, status

from app.core.config import settings

# Sent as meta_data.order_reference so a checkout can be traced back from the
# KutanaPay dashboard without opening our database.
_REFERENCE_PREFIX = "odos"

PAID_STATUSES = frozenset({"paid", "completed", "approved"})
PENDING_STATUSES = frozenset({"pending", "processing", "created"})
FAILED_STATUSES = frozenset({"failed", "rejected", "cancelled", "expired", "timeout"})

# The documented signature header, and the prefix its value carries.
SIGNATURE_HEADER = "X-Webhook-Signature"
_SIGNATURE_PREFIX = "sha256="

_TIMEOUT_SECONDS = 30


def generate_reference() -> str:
    """Our own reference for the checkout.

    KutanaPay issues its own ``payment_reference`` (``PAY-K7M2Q9X4``), but the
    order has to be findable before that value exists -- the row is written
    before the gateway is called. This is what the PaymentTransaction is keyed
    on; the gateway's reference is stored alongside once it answers.
    """
    return f"{_REFERENCE_PREFIX}-{uuid.uuid4().hex}"


def ensure_kutanapay_configured() -> None:
    if not settings.kutanapay_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card and mobile money payments are temporarily unavailable.",
        )


def _headers() -> dict[str, str]:
    return {
        "x-api-key": settings.kutanapay_api_key.strip(),
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    return f"{settings.kutanapay_base_url.rstrip('/')}{path}"


def _unwrap(payload: Any, *, context: str) -> dict[str, Any]:
    """Pull ``data`` out of KutanaPay's ``{data, message, status}`` envelope.

    Raises rather than returning a half-answer: a checkout we cannot read is a
    checkout we must not treat as paid.
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The payment provider returned an unreadable response ({context}).",
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The payment provider returned no checkout details ({context}).",
        )
    return data


def create_checkout(
    *,
    amount: float,
    reference: str,
    customer_email: str,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    description: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Create a hosted checkout and return KutanaPay's ``data`` object.

    ``amount`` is in cedis, not pesewas -- see the module docstring.
    """
    ensure_kutanapay_configured()

    body: dict[str, Any] = {
        "customer_email": customer_email,
        "amount": round(float(amount), 2),
        "currency_code": settings.kutanapay_currency,
        "meta_data": {
            "order_reference": reference,
            "source": "odos-mobile",
        },
    }
    if customer_name:
        body["customer_name"] = customer_name
    if customer_phone:
        body["customer_phone"] = customer_phone
    if description:
        body["description"] = description
    if order_id:
        body["meta_data"]["order_id"] = order_id

    try:
        response = requests.post(
            _url("/api/v1/merchants/checkouts"),
            json=body,
            headers=_headers(),
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't reach the payment provider. Please try again.",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment provider rejected this checkout. Please try again.",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment provider returned an unreadable response.",
        ) from exc

    data = _unwrap(payload, context="create")
    if not data.get("checkout_url"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment provider did not return a checkout link.",
        )
    return data


def get_checkout(checkout_id: str) -> dict[str, Any]:
    """Read one checkout. Used to reconcile when a webhook is missed."""
    ensure_kutanapay_configured()

    try:
        response = requests.get(
            _url(f"/api/v1/merchants/checkouts/{checkout_id}"),
            headers=_headers(),
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't reach the payment provider. Please try again.",
        ) from exc

    if response.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find that payment with the provider.",
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment provider could not confirm this payment.",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The payment provider returned an unreadable response.",
        ) from exc

    return _unwrap(payload, context="status")


def cancel_checkout(checkout_id: str) -> bool:
    """Cancel a still-pending checkout. Returns False rather than raising.

    Cancellation is housekeeping -- a paid or already-closed checkout simply
    cannot be cancelled, and that is not an error worth failing a request over.
    """
    if not settings.kutanapay_is_configured:
        return False
    try:
        response = requests.post(
            _url(f"/api/v1/merchants/checkouts/{checkout_id}/cancel"),
            headers=_headers(),
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return False
    return response.status_code < 400


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Check ``X-Webhook-Signature`` against the raw body.

    The body must be the bytes as received. Re-serialising parsed JSON changes
    key order and whitespace, and the HMAC then never matches.
    """
    secret = (settings.kutanapay_webhook_secret or "").strip()
    if not secret or not signature_header:
        return False
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        return False

    expected = signature_header[len(_SIGNATURE_PREFIX):]
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)


def normalize_status(raw_status: Any) -> str:
    """Collapse KutanaPay's statuses onto paid / pending / failed.

    Anything unrecognised is treated as pending rather than failed: a status we
    do not know about is a reason to ask again, not a reason to tell a customer
    their payment did not work.
    """
    value = str(raw_status or "").strip().lower()
    if value in PAID_STATUSES:
        return "paid"
    if value in FAILED_STATUSES:
        return "failed"
    return "pending"


def parse_amount_to_subunit(raw_amount: Any) -> int | None:
    """Convert a KutanaPay major-unit amount to pesewas for the ledger.

    Rounded rather than truncated: ``0.29 * 100`` is ``28.999999999999996`` in
    IEEE 754, and ``int()`` on that records 28 -- under-counting the customer's
    payment by a pesewa and failing the amount check on a perfectly good
    payment. (0.57, 1.13 and 1.15 behave the same way; 12.34, which the retired
    iPay module cited, does not.)
    """
    if raw_amount is None:
        return None
    try:
        return int(round(float(raw_amount) * 100))
    except (TypeError, ValueError):
        return None
