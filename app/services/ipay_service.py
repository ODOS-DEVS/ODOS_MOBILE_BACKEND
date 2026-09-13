"""iPay (ipaygh.com) collections.

Two things about this gateway shape the code below, and both differ from
Paystack.

**There is no JSON initiate.** `/gateway/checkout` takes an HTML form POST and
answers with a page, so there is no authorization_url to hand the app. The
caller has to render a self-submitting form instead -- see
`ipay_checkout_redirect` in the payment controller.

**The IPN is unauthenticated.** iPay notifies by doing a bare
`GET <ipn_url>?invoice_id=...`: no signature, no shared secret, nothing to
verify against. Paystack signs with HMAC-SHA512 and `verify_webhook_signature`
checks it; there is no equivalent here, and none is offered in their docs. So
the notification is only a prompt to go and ask -- `check_status` is the sole
source of truth, and even its answer is treated as untrusted input by the
caller, which re-checks the amount against the order before releasing anything.
"""

from __future__ import annotations

import uuid
from typing import Any

import requests
from fastapi import HTTPException, status

from app.core.config import settings

# iPay caps invoice_id at 25 characters, so the `odos-{uuid4().hex}` reference
# used for Paystack (37 chars) cannot be reused here.
INVOICE_ID_MAX_LENGTH = 25
_REFERENCE_PREFIX = "odos"

PAID_STATUS = "paid"
CANCELLED_STATUS = "cancelled"
FAILED_STATUS = "failed"
# "error" is what the gateway returns when it cannot find the invoice yet, which
# is a lookup problem rather than a refusal -- treating it as pending leaves it
# open for the reconciliation loop instead of failing a payment that may land.
PENDING_STATUSES = frozenset({"new", "awaiting_payment", "error"})


def generate_invoice_id() -> str:
    """A reference that fits iPay's 25-character invoice_id limit.

    20 hex characters is 80 bits of entropy; the unique index on
    payment_transactions.reference is what actually guarantees uniqueness.
    """
    candidate = f"{_REFERENCE_PREFIX}{uuid.uuid4().hex[:20]}"
    assert len(candidate) <= INVOICE_ID_MAX_LENGTH
    return candidate


def ensure_ipay_configured() -> None:
    if not settings.ipay_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="iPay is not configured on this environment yet.",
        )


def build_checkout_fields(
    *,
    invoice_id: str,
    total: str,
    success_url: str,
    cancelled_url: str,
    ipn_url: str,
    customer_name: str | None = None,
    customer_mobile: str | None = None,
    customer_email: str | None = None,
    description: str | None = None,
) -> dict[str, str]:
    """The form fields to POST to /gateway/checkout.

    `total` is a decimal GHS string ("12.50"), not a subunit integer -- iPay
    differs from Paystack here, and sending pesewas would silently charge the
    customer a hundred times the intended amount.
    """
    ensure_ipay_configured()
    if len(invoice_id) > INVOICE_ID_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generated payment reference is too long for iPay.",
        )

    fields = {
        "merchant_key": settings.ipay_merchant_key.strip(),
        "invoice_id": invoice_id,
        "total": total,
        "success_url": success_url,
        "cancelled_url": cancelled_url,
        "ipn_url": ipn_url,
    }
    merchant_code = settings.ipay_merchant_code.strip()
    if merchant_code:
        fields["merchant_code"] = merchant_code
    if customer_name:
        fields["extra_name"] = customer_name
    if customer_mobile:
        fields["extra_mobile"] = customer_mobile
    if customer_email:
        fields["extra_email"] = customer_email
    if description:
        fields["description"] = description
    return fields


def checkout_url() -> str:
    return f"{settings.ipay_base_url.rstrip('/')}/gateway/checkout"


def check_status(invoice_id: str) -> dict[str, Any]:
    """Ask iPay what actually happened to an invoice.

    This is the only trustworthy signal the gateway offers. Network and parse
    failures raise rather than returning a falsy status, so that a transient
    outage can never be mistaken for a non-payment.
    """
    ensure_ipay_configured()
    try:
        response = requests.get(
            f"{settings.ipay_base_url.rstrip('/')}/gateway/json_status_chk",
            params={
                "merchant_key": settings.ipay_merchant_key.strip(),
                "invoice_id": invoice_id,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach iPay to confirm this payment.",
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="iPay returned an unreadable response.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="iPay returned an unexpected response shape.",
        )
    return _unwrap_status_payload(payload, invoice_id)


def _unwrap_status_payload(payload: dict[str, Any], invoice_id: str) -> dict[str, Any]:
    """Flatten the envelope the live gateway actually returns.

    The docs show a flat object, but the live endpoint nests the record under
    the invoice id::

        {"odos123": {"status": "paid", "amount": "12.50", ...}}

    Read flat, `status` is absent and every real payment normalises to a
    failure. Both shapes are accepted so that a future change back to the
    documented form does not break confirmation.
    """
    nested = payload.get(invoice_id)
    if isinstance(nested, dict):
        return nested
    # Single-entry envelope whose key differs only in case/whitespace.
    if len(payload) == 1:
        (only_value,) = payload.values()
        if isinstance(only_value, dict) and "status" in only_value:
            return only_value
    return payload


def parse_amount_to_subunit(raw_amount: Any) -> int | None:
    """iPay reports amounts as GHS decimals; the ledger counts pesewas.

    Rounded rather than truncated: float("12.34") * 100 is 1233.9999999999998,
    and int() on that would under-count the customer's payment by a pesewa and
    fail the amount check on a perfectly good transaction.
    """
    if raw_amount is None:
        return None
    try:
        return int(round(float(raw_amount) * 100))
    except (TypeError, ValueError):
        return None


def normalize_status(raw_status: Any) -> str:
    """Map an iPay status onto the vocabulary payment_transactions uses."""
    value = str(raw_status or "").strip().lower()
    if value == PAID_STATUS:
        return "paid"
    if value == CANCELLED_STATUS:
        return "cancelled"
    if value == FAILED_STATUS:
        return "failed"
    # Anything unrecognised stays pending: it must never release goods, and
    # marking it failed would close off a payment that might still settle.
    return "pending"
