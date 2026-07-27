from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode
from typing import Any

import requests
from fastapi import HTTPException, status

from app.core.config import settings

PAYSTACK_API_BASE_URL = "https://api.paystack.co"


def ensure_paystack_configured() -> None:
    if not settings.paystack_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paystack is not configured on this environment yet.",
        )


def _headers() -> dict[str, str]:
    ensure_paystack_configured()
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key.strip()}",
        "Content-Type": "application/json",
    }


def _parse_json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack returned an unreadable response.",
        ) from exc

    if response.ok and payload.get("status") is True:
        return payload

    detail = payload.get("message") or "Paystack request failed."
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


def initialize_transaction(
    *,
    email: str,
    amount_subunit: int,
    reference: str,
    callback_url: str,
    cancel_url: str | None,
    currency: str,
    channels: list[str] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_subunit,
        "reference": reference,
        "currency": currency,
        "callback_url": callback_url,
    }
    if channels:
        payload["channels"] = channels
    merged_metadata = dict(metadata or {})
    if cancel_url:
        merged_metadata["cancel_action"] = cancel_url
    if merged_metadata:
        payload["metadata"] = merged_metadata

    response = requests.post(
        f"{PAYSTACK_API_BASE_URL}/transaction/initialize",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    return _parse_json_response(response)


def verify_transaction(reference: str) -> dict[str, Any]:
    response = requests.get(
        f"{PAYSTACK_API_BASE_URL}/transaction/verify/{reference}",
        headers=_headers(),
        timeout=30,
    )
    return _parse_json_response(response)


def list_transfer_institutions(
    *,
    payout_method_type: str,
    currency: str,
) -> list[dict[str, Any]]:
    requested_type = (
        "mobile_money" if payout_method_type == "mobile_money" else "ghipss"
    )
    query = urlencode(
        {
            "currency": currency.upper(),
            "type": requested_type,
        }
    )
    response = requests.get(
        f"{PAYSTACK_API_BASE_URL}/bank?{query}",
        headers=_headers(),
        timeout=30,
    )
    payload = _parse_json_response(response)
    banks = payload.get("data")
    if isinstance(banks, list):
        return [entry for entry in banks if isinstance(entry, dict)]
    return []


def resolve_account_number(
    *,
    account_number: str,
    bank_code: str,
) -> dict[str, Any]:
    query = urlencode(
        {
            "account_number": account_number,
            "bank_code": bank_code,
        }
    )
    response = requests.get(
        f"{PAYSTACK_API_BASE_URL}/bank/resolve?{query}",
        headers=_headers(),
        timeout=30,
    )
    payload = _parse_json_response(response)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack did not return a valid account resolution response.",
        )
    return data


def create_transfer_recipient(
    *,
    recipient_type: str,
    name: str,
    account_number: str,
    bank_code: str,
    currency: str,
    description: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": recipient_type,
        "name": name,
        "account_number": account_number,
        "bank_code": bank_code,
        "currency": currency.upper(),
    }
    if description:
        payload["description"] = description

    response = requests.post(
        f"{PAYSTACK_API_BASE_URL}/transferrecipient",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    parsed = _parse_json_response(response)
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack did not return a valid transfer recipient response.",
        )
    return data


def initiate_transfer(
    *,
    amount_subunit: int,
    recipient_code: str,
    reference: str,
    reason: str | None,
    currency: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "balance",
        "amount": amount_subunit,
        "recipient": recipient_code,
        "reference": reference,
        "currency": currency.upper(),
    }
    if reason:
        payload["reason"] = reason

    response = requests.post(
        f"{PAYSTACK_API_BASE_URL}/transfer",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    parsed = _parse_json_response(response)
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack did not return a valid transfer response.",
        )
    return data


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = (settings.paystack_webhook_secret or settings.paystack_secret_key).strip()
    if not secret or not signature:
        return False
    computed_signature = hmac.new(
        secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(computed_signature, signature)
