from __future__ import annotations

import hashlib
import html
import json
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.controllers.customer_wallet_controller import reconcile_wallet_topup_by_reference
from app.controllers.finance_controller import record_payment_collection
from app.controllers.order_controller import (
    _broadcast_order_realtime,
    _dispatch_order_push,
    _dispatch_vendor_new_order_alerts,
    activate_order_after_payment,
    prepare_order_for_checkout,
)
from app.controllers.wallet_controller import (
    publish_vendor_wallet_updates,
    reconcile_paystack_transfer_event,
)
from app.core.config import settings
from app.core.event_types import CHECKOUT_STARTED, PAYMENT_ATTEMPT
from app.helpers.event_context import request_ip, request_user_agent
from app.models import Order, PaymentTransaction, PaymentWebhookEvent, User
from app.schemas.order import OrderRead
from app.schemas.payment import (
    CheckoutSessionCreate,
    CheckoutSessionRead,
    PaymentVerificationRead,
)
from app.services.event_log_service import record_user_event
from app.services.finance_math import amount_from_subunit, amount_to_subunit
from app.services.kutanapay_service import (
    create_checkout as kutanapay_create_checkout,
)
from app.services.kutanapay_service import (
    ensure_kutanapay_configured,
)
from app.services.kutanapay_service import (
    generate_reference as generate_kutanapay_reference,
)
from app.services.kutanapay_service import (
    get_checkout as kutanapay_get_checkout,
)
from app.services.kutanapay_service import (
    normalize_status as kutanapay_normalize_status,
)
from app.services.kutanapay_service import (
    parse_amount_to_subunit as kutanapay_parse_amount_to_subunit,
)
from app.services.kutanapay_service import (
    verify_webhook_signature as verify_kutanapay_signature,
)
from app.services.paystack_service import (
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)

PENDING_PROVIDER_STATUSES = {"pending", "ongoing", "processing", "queued"}
CANCELLED_PROVIDER_STATUSES = {"abandoned", "cancelled"}
FAILED_PROVIDER_STATUSES = {"failed", "reversed"}


def _append_query_params(url: str, **params: str | None) -> str:
    parsed_url = urlsplit(url)
    query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    for key, value in params.items():
        if value is not None:
            query[key] = value
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query),
            parsed_url.fragment,
        )
    )


def _merge_query_params(url: str, params: dict[str, str | None]) -> str:
    parsed_url = urlsplit(url)
    query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    for key, value in params.items():
        if value is not None:
            query[key] = value
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query),
            parsed_url.fragment,
        )
    )


def _preferred_channel(payment_type: str) -> str | None:
    normalized = payment_type.strip().lower()
    if normalized == "card":
        return "card"
    if normalized in {"momo", "mobile_money"}:
        return "mobile_money"
    return None


def _serialize_payment_verification(
    order: Order,
    payment_transaction: PaymentTransaction,
    *,
    provider_status: str,
    message: str,
) -> PaymentVerificationRead:
    return PaymentVerificationRead(
        order=OrderRead.model_validate(order),
        reference=payment_transaction.reference,
        payment_status=order.payment_status,
        provider_status=provider_status,
        paid_at=payment_transaction.paid_at,
        verified_at=payment_transaction.verified_at,
        message=message,
    )


def _load_payment_transaction_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    reference: str,
) -> PaymentTransaction:
    transaction = db.scalar(
        select(PaymentTransaction)
        .options(
            selectinload(PaymentTransaction.order).selectinload(Order.items),
            selectinload(PaymentTransaction.order).selectinload(Order.return_requests),
            selectinload(PaymentTransaction.order).selectinload(Order.user),
        )
        .where(
            PaymentTransaction.reference == reference,
            PaymentTransaction.user_id == user_id,
        )
    )
    if not transaction or not transaction.order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That payment session was not found.",
        )
    return transaction


def _mark_payment_as_unsuccessful(
    order: Order,
    payment_transaction: PaymentTransaction,
    *,
    provider_status: str,
    gateway_response: str | None,
    now: datetime,
) -> None:
    if provider_status in CANCELLED_PROVIDER_STATUSES:
        mapped_status = "cancelled"
    elif provider_status in FAILED_PROVIDER_STATUSES:
        mapped_status = "failed"
    else:
        mapped_status = "pending"
    payment_transaction.status = mapped_status
    payment_transaction.gateway_response = gateway_response
    payment_transaction.verified_at = now
    payment_transaction.last_checked_at = now
    if order.payment_status != "paid":
        order.payment_status = mapped_status


def _format_gateway_response(gateway_response: str | None) -> str | None:
    if not gateway_response:
        return None
    cleaned = gateway_response.strip()
    if not cleaned:
        return None
    if cleaned.endswith((".", "!", "?")):
        return cleaned
    return f"{cleaned}."


def _unsuccessful_payment_message(
    *,
    payment_status: str,
    gateway_response: str | None,
) -> str:
    formatted_gateway_response = _format_gateway_response(gateway_response)
    if payment_status == "cancelled":
        return formatted_gateway_response or "Payment was cancelled before completion."
    if payment_status == "pending":
        return (
            formatted_gateway_response
            or "We're still waiting for Paystack to finish confirming this payment."
        )
    return formatted_gateway_response or "This payment did not complete successfully."


def _apply_successful_payment(
    db: Session,
    payment_transaction: PaymentTransaction,
    provider_payload: dict,
) -> PaymentVerificationRead:
    order = payment_transaction.order
    if not order or not order.user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't load the order tied to this payment.",
        )

    # Serialize concurrent confirmations of the same payment.
    #
    # The webhook and the client's own verify call routinely arrive together —
    # the app calls verify the moment Paystack returns, which is roughly when
    # the webhook fires. Without this lock both read payment_status as not-paid,
    # both proceed, and the second collection is stopped only by the ledger's
    # (payment_transaction_id, kind) unique constraint. The money stays correct,
    # but the caller gets a 500 and Paystack retries an event that was fine.
    #
    # populate_existing is required: SQLAlchemy would otherwise hand back the
    # already-loaded instance from the identity map, so the lock would be held
    # while payment_status was still read from a stale in-memory value.
    locked_order = db.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_order is not None:
        order = locked_order

    now = datetime.now(UTC)
    paid_at = provider_payload.get("paid_at") or provider_payload.get("created_at")
    preferred_paid_at = None
    if isinstance(paid_at, str) and paid_at:
        try:
            preferred_paid_at = datetime.fromisoformat(paid_at.replace("Z", "+00:00"))
        except ValueError:
            preferred_paid_at = None

    payment_transaction.status = "paid"
    payment_transaction.gateway_response = provider_payload.get("gateway_response")
    payment_transaction.provider_transaction_id = (
        str(provider_payload.get("id")) if provider_payload.get("id") is not None else None
    )
    payment_transaction.processor_fee_subunit = int(provider_payload.get("fees") or 0)
    payment_transaction.authorization_data = provider_payload.get("authorization")
    payment_transaction.raw_response = provider_payload
    payment_transaction.paid_at = preferred_paid_at or payment_transaction.paid_at or now
    payment_transaction.verified_at = now
    payment_transaction.last_checked_at = now

    first_time_payment = order.payment_status != "paid"
    if first_time_payment:
        payment_event = activate_order_after_payment(db, order.user, order)
        order.payment_reference = payment_transaction.reference
        order.payment_provider = payment_transaction.provider
        order.payment_status = "paid"
        order.paid_at = payment_transaction.paid_at
        record_payment_collection(
            db,
            order,
            payment_transaction,
            processor_fee_amount=amount_from_subunit(payment_transaction.processor_fee_subunit),
        )
        db.commit()
        db.refresh(order)
        _dispatch_order_push(
            user=order.user,
            title="Payment confirmed",
            body=f"Order #{order.order_number} is now being prepared.",
            order=order,
            notification_event=payment_event,
        )
        db.commit()
        db.refresh(order)
        _broadcast_order_realtime(db, order)
        _dispatch_vendor_new_order_alerts(db, order)
        db.commit()
    else:
        # Already confirmed by whichever caller got here first. Committing the
        # provider-metadata refresh above is enough; re-running collection would
        # be caught by a constraint, which is not how a duplicate should surface.
        db.commit()
        db.refresh(order)

    return _serialize_payment_verification(
        order,
        payment_transaction,
        provider_status="success",
        message="Payment confirmed successfully.",
    )


def _reconcile_payment_transaction(
    db: Session,
    payment_transaction: PaymentTransaction,
    provider_payload: dict,
) -> PaymentVerificationRead:
    order = payment_transaction.order
    if not order:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The order linked to this payment could not be found.",
    )

    provider_status = str(provider_payload.get("status") or "").strip().lower()
    amount_subunit = int(provider_payload.get("amount") or 0)
    currency = str(provider_payload.get("currency") or payment_transaction.currency).upper()
    now = datetime.now(UTC)

    payment_transaction.raw_response = provider_payload
    payment_transaction.last_checked_at = now

    if amount_subunit != payment_transaction.amount_subunit or currency != payment_transaction.currency:
        payment_transaction.status = "failed"
        payment_transaction.verified_at = now
        payment_transaction.gateway_response = "Transaction amount or currency mismatch."
        order.payment_status = "failed"
        db.commit()
        db.refresh(order)
        return _serialize_payment_verification(
            order,
            payment_transaction,
            provider_status=provider_status or "failed",
            message="Payment verification failed because the amount did not match the order.",
        )

    if provider_status == "success":
        return _apply_successful_payment(db, payment_transaction, provider_payload)

    if provider_status in PENDING_PROVIDER_STATUSES:
        _mark_payment_as_unsuccessful(
            order,
            payment_transaction,
            provider_status="pending",
            gateway_response=provider_payload.get("gateway_response"),
            now=now,
        )
        db.commit()
        db.refresh(order)
        return _serialize_payment_verification(
            order,
            payment_transaction,
            provider_status=provider_status,
            message=_unsuccessful_payment_message(
                payment_status=payment_transaction.status,
                gateway_response=provider_payload.get("gateway_response"),
            ),
        )

    _mark_payment_as_unsuccessful(
        order,
        payment_transaction,
        provider_status=provider_status or "failed",
        gateway_response=provider_payload.get("gateway_response"),
        now=now,
    )
    db.commit()
    db.refresh(order)
    return _serialize_payment_verification(
        order,
        payment_transaction,
        provider_status=provider_status or "failed",
        message=_unsuccessful_payment_message(
            payment_status=payment_transaction.status,
            gateway_response=provider_payload.get("gateway_response"),
        ),
    )


def create_checkout_session(
    db: Session,
    request: Request,
    current_user: User,
    payload: CheckoutSessionCreate,
) -> CheckoutSessionRead:
    if payload.payment_type.strip().lower() == "wallet":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="In-app wallet payments must use POST /wallet/customer/checkout.",
        )

    reference = f"odos-{uuid.uuid4().hex}"
    order = prepare_order_for_checkout(
        db,
        current_user,
        payload,
        payment_provider="paystack",
        payment_reference=reference,
    )
    app_callback_url = _append_query_params(
        payload.callback_url or "odosmobileexpo://payments/return",
        orderId=str(order.id),
    )
    app_cancel_url = _append_query_params(
        payload.cancel_url or payload.callback_url or "odosmobileexpo://payments/return",
        orderId=str(order.id),
        cancelled="1",
        reference=reference,
    )
    callback_url = _append_query_params(
        str(request.url_for("paystack_checkout_redirect")),
        return_url=app_callback_url,
    )
    cancel_url = _append_query_params(
        str(request.url_for("paystack_checkout_redirect")),
        return_url=app_cancel_url,
    )
    preferred_channels = _preferred_channel(payload.payment_type)
    try:
        paystack_response = initialize_transaction(
            email=current_user.email,
            amount_subunit=amount_to_subunit(order.total_amount),
            reference=reference,
            callback_url=callback_url,
            cancel_url=cancel_url,
            currency=settings.paystack_currency,
            channels=preferred_channels,
            metadata={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "user_id": str(current_user.id),
                "payment_type": payload.payment_type,
            },
        )
    except HTTPException as exc:
        if preferred_channels and exc.status_code == status.HTTP_502_BAD_GATEWAY:
            paystack_response = initialize_transaction(
                email=current_user.email,
                amount_subunit=amount_to_subunit(order.total_amount),
                reference=reference,
                callback_url=callback_url,
                cancel_url=cancel_url,
                currency=settings.paystack_currency,
                channels=None,
                metadata={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "user_id": str(current_user.id),
                    "payment_type": payload.payment_type,
                },
            )
        else:
            raise
    response_data = paystack_response.get("data", {})
    transaction = PaymentTransaction(
        order_id=order.id,
        user_id=current_user.id,
        provider="paystack",
        reference=reference,
        access_code=response_data.get("access_code"),
        authorization_url=response_data.get("authorization_url"),
        currency=settings.paystack_currency,
        amount_subunit=amount_to_subunit(order.total_amount),
        status="pending",
        preferred_channel=_preferred_channel(payload.payment_type),
    )
    db.add(transaction)
    db.commit()
    record_user_event(
        db,
        user_id=str(current_user.id),
        event_type=CHECKOUT_STARTED,
        action="commerce.checkout_started",
        entity_type="order",
        entity_id=str(order.id),
        metadata={
            "order_number": order.order_number,
            "payment_type": payload.payment_type,
            "amount": order.total_amount,
        },
        ip_address=request_ip(request),
        user_agent=request_user_agent(request),
    )
    record_user_event(
        db,
        user_id=str(current_user.id),
        event_type=PAYMENT_ATTEMPT,
        action="commerce.payment_attempt",
        entity_type="payment_transaction",
        entity_id=reference,
        metadata={
            "order_id": str(order.id),
            "provider": "paystack",
            "amount": order.total_amount,
        },
        ip_address=request_ip(request),
        user_agent=request_user_agent(request),
    )
    return CheckoutSessionRead(
        order_id=order.id,
        order_number=order.order_number,
        reference=reference,
        authorization_url=response_data["authorization_url"],
        access_code=response_data["access_code"],
        amount=order.total_amount,
        currency=settings.paystack_currency,
        payment_status=order.payment_status,
    )


def paystack_checkout_redirect(
    request: Request,
    *,
    return_url: str,
) -> HTMLResponse:
    merged_return_url = _merge_query_params(
        return_url,
        {
            key: value
            for key, value in request.query_params.items()
            if key != "return_url"
        },
    )
    escaped_return_url = html.escape(merged_return_url, quote=True)
    html_body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="refresh" content="0;url={escaped_return_url}" />
    <title>Returning to ODOS</title>
    <style>
      :root {{
        color-scheme: light;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #f8fafc;
        color: #0f172a;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      .card {{
        width: min(92vw, 28rem);
        background: #ffffff;
        border-radius: 1.5rem;
        padding: 2rem;
        box-shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
        text-align: center;
      }}
      h1 {{
        margin: 0 0 0.75rem;
        font-size: 1.2rem;
      }}
      p {{
        margin: 0 0 1.25rem;
        color: #475569;
        line-height: 1.5;
      }}
      a {{
        display: inline-block;
        padding: 0.85rem 1.2rem;
        border-radius: 999px;
        background: #111827;
        color: #ffffff;
        text-decoration: none;
        font-weight: 600;
      }}
    </style>
  </head>
  <body>
    <main class="card">
      <h1>Returning to ODOS</h1>
      <p>Your payment is done here. We’re sending you back to the app now.</p>
      <a href="{escaped_return_url}">Return to ODOS</a>
    </main>
    <script>
      window.location.replace({json.dumps(merged_return_url)});
    </script>
  </body>
</html>"""
    return HTMLResponse(content=html_body)


def verify_checkout_session(
    db: Session,
    current_user: User,
    reference: str,
) -> PaymentVerificationRead:
    payment_transaction = _load_payment_transaction_for_user(
        db,
        user_id=current_user.id,
        reference=reference,
    )
    # Dispatch on the provider that actually took the money. Sending a
    # KutanaPay reference to Paystack's verify endpoint returns "transaction
    # not found", which would surface to the customer as a failed payment they
    # had in fact completed.
    if payment_transaction.provider == "kutanapay":
        return _verify_kutanapay_payment(db, payment_transaction)
    if payment_transaction.provider == "ipay":
        # iPay was retired as the collections gateway. Rows created while it
        # was live still exist and must not be re-verified against Paystack,
        # which would report a completed payment as failed. They are terminal:
        # whatever they settled as at the time is what they stay.
        return _serialize_payment_verification(
            payment_transaction.order,
            payment_transaction,
            provider_status=payment_transaction.status,
            message="This payment was taken by a provider we no longer use. "
            "Its recorded status is final.",
        )
    verification_response = verify_transaction(reference)
    provider_payload = verification_response.get("data", {})
    return _reconcile_payment_transaction(db, payment_transaction, provider_payload)


def handle_paystack_webhook(
    db: Session,
    *,
    raw_body: bytes,
    signature: str | None,
) -> dict[str, bool]:
    if not verify_webhook_signature(raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Paystack webhook signature.",
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The webhook payload was not valid JSON.",
        ) from exc

    event_type = str(payload.get("event") or "").strip().lower()
    event_data = payload.get("data") or {}
    reference = event_data.get("reference")

    # Two dedup keys, because they fail in different ways.
    #
    # The raw-body digest catches a byte-identical retry, which is the common
    # case. It misses a retry whose body differs in any field — a timestamp, an
    # ordering change — and then the same real-world event is processed twice.
    #
    # The semantic digest covers that: provider, event type, reference and the
    # provider's own transaction id identify the event itself rather than its
    # encoding. Downstream guards would still keep the money correct, but a
    # duplicate should be recognised here rather than surfacing as a constraint
    # violation three layers down.
    provider_event_id = event_data.get("id")
    semantic_digest = hashlib.sha256(
        "|".join(
            [
                "paystack",
                event_type,
                str(reference or ""),
                str(provider_event_id or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    event_digest = hashlib.sha256(raw_body).hexdigest()

    # A semantic match only counts once the earlier delivery was actually
    # processed. A previous attempt that failed mid-flight must be allowed to
    # run again, which is the whole point of the provider retrying.
    existing_event = db.scalar(
        select(PaymentWebhookEvent).where(
            or_(
                PaymentWebhookEvent.event_digest == event_digest,
                and_(
                    PaymentWebhookEvent.event_digest == semantic_digest,
                    PaymentWebhookEvent.processing_status == "processed",
                ),
            )
        )
    )
    if existing_event:
        return {"received": True}

    webhook_event = PaymentWebhookEvent(
        provider="paystack",
        event_type=event_type,
        event_digest=event_digest,
        reference=reference,
        signature=signature,
        payload=payload,
    )
    db.add(webhook_event)

    # The semantic key is recorded as its own row so a later, differently-encoded
    # delivery of the same event matches it. Kept as a separate row rather than a
    # new column so the existing unique index on event_digest does the enforcing.
    if provider_event_id is not None and semantic_digest != event_digest:
        db.add(
            PaymentWebhookEvent(
                provider="paystack",
                event_type=event_type,
                event_digest=semantic_digest,
                reference=reference,
                signature=None,
                payload={"semantic_key_for": event_digest},
                processing_status="processed",
                processed_at=datetime.now(UTC),
            )
        )
    db.flush()

    try:
        if event_type == "charge.success" and reference:
            payment_transaction = db.scalar(
                select(PaymentTransaction)
                .options(
                    selectinload(PaymentTransaction.order).selectinload(Order.items),
                    selectinload(PaymentTransaction.order).selectinload(Order.return_requests),
                    selectinload(PaymentTransaction.order).selectinload(Order.user),
                )
                .where(PaymentTransaction.reference == reference)
            )
            if payment_transaction:
                verification_response = verify_transaction(reference)
                _reconcile_payment_transaction(
                    db,
                    payment_transaction,
                    verification_response.get("data", {}),
                )
            else:
                reconcile_wallet_topup_by_reference(db, reference)
        elif event_type in {"transfer.success", "transfer.failed", "transfer.reversed"} and reference:
            changed_vendor_id = reconcile_paystack_transfer_event(
                db,
                reference=reference,
                event_type=event_type,
                transfer_payload=event_data if isinstance(event_data, dict) else {},
            )
            if changed_vendor_id:
                publish_vendor_wallet_updates(changed_vendor_id)
        webhook_event.processing_status = "processed"
        webhook_event.processed_at = datetime.now(UTC)
        db.commit()
        return {"received": True}
    except Exception as exc:
        webhook_event.processing_status = "failed"
        webhook_event.failure_reason = str(exc)
        webhook_event.processed_at = datetime.now(UTC)
        db.commit()
        raise


# --------------------------------------------------------------------------
# KutanaPay hosted checkout
#
# Replaced iPay as the collections gateway. The flow is closer to Paystack's
# than iPay's was:
#
#   checkout  -> the gateway returns a checkout_url, handed straight to the app
#   webhook   -> signed with HMAC-SHA256 over the raw body, so the callback is
#                evidence rather than a hint; the amount is still re-checked
#                against the order, because a signature proves origin and not
#                correctness
#
# Payouts stay on Paystack; KutanaPay's public API covers checkouts only.
# --------------------------------------------------------------------------












def create_kutanapay_checkout_session(
    db: Session,
    request: Request,
    current_user: User,
    payload: CheckoutSessionCreate,
) -> CheckoutSessionRead:
    if payload.payment_type.strip().lower() == "wallet":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="In-app wallet payments must use POST /wallet/customer/checkout.",
        )
    ensure_kutanapay_configured()

    reference = generate_kutanapay_reference()
    order = prepare_order_for_checkout(
        db,
        current_user,
        payload,
        payment_provider="kutanapay",
        payment_reference=reference,
    )

    checkout = kutanapay_create_checkout(
        # Cedis, not pesewas. KutanaPay quotes major units; the ledger below
        # stores subunits, which is why the two are converted separately.
        amount=order.total_amount,
        reference=reference,
        customer_email=current_user.email,
        customer_name=current_user.full_name,
        customer_phone=current_user.phone_number,
        description=f"Order {order.order_number}",
        order_id=str(order.id),
    )

    transaction = PaymentTransaction(
        order_id=order.id,
        user_id=current_user.id,
        provider="kutanapay",
        reference=reference,
        access_code=None,
        authorization_url=checkout["checkout_url"],
        currency=settings.kutanapay_currency,
        amount_subunit=amount_to_subunit(order.total_amount),
        status="pending",
        preferred_channel=None,
        # The gateway's own id, which GET and cancel are keyed on. It is not
        # our `reference`. Kept in the indexed column rather than in JSON
        # because the webhook looks a transaction up by it on every delivery,
        # and because _apply_successful_payment overwrites authorization_data.
        provider_transaction_id=str(checkout["id"]) if checkout.get("id") else None,
        authorization_data={
            "payment_reference": checkout.get("payment_reference"),
            "expires_at": checkout.get("expires_at"),
        },
    )
    db.add(transaction)
    db.commit()

    record_user_event(
        db,
        user_id=str(current_user.id),
        event_type=CHECKOUT_STARTED,
        action="commerce.checkout_started",
        entity_type="order",
        entity_id=str(order.id),
        metadata={
            "order_number": order.order_number,
            "payment_type": payload.payment_type,
            "amount": order.total_amount,
        },
        ip_address=request_ip(request),
        user_agent=request_user_agent(request),
    )
    record_user_event(
        db,
        user_id=str(current_user.id),
        event_type=PAYMENT_ATTEMPT,
        action="commerce.payment_attempt",
        entity_type="payment_transaction",
        entity_id=reference,
        metadata={
            "order_id": str(order.id),
            "provider": "kutanapay",
            "amount": order.total_amount,
        },
        ip_address=request_ip(request),
        user_agent=request_user_agent(request),
    )

    return CheckoutSessionRead(
        order_id=order.id,
        order_number=order.order_number,
        reference=reference,
        authorization_url=checkout["checkout_url"],
        access_code=None,
        amount=order.total_amount,
        currency=settings.kutanapay_currency,
        payment_status=order.payment_status,
    )


def _kutanapay_checkout_id(transaction: PaymentTransaction) -> str | None:
    if transaction.provider_transaction_id:
        return transaction.provider_transaction_id
    data = transaction.authorization_data
    if isinstance(data, dict):
        return data.get("payment_reference")
    return None


def _verify_kutanapay_payment(
    db: Session,
    transaction: PaymentTransaction,
) -> PaymentVerificationRead:
    """Ask KutanaPay what happened, and decide from the answer alone.

    Shared by the webhook and by the app's own verify call, so a payment is
    judged the same way whichever arrives first -- and they routinely race.
    """
    order = transaction.order
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't load the order tied to this payment.",
        )

    checkout_id = _kutanapay_checkout_id(transaction)
    if not checkout_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This payment has no provider reference to check.",
        )

    payload = kutanapay_get_checkout(checkout_id)
    gateway_status = kutanapay_normalize_status(payload.get("status"))
    now = datetime.now(UTC)
    transaction.raw_response = payload
    transaction.last_checked_at = now

    if gateway_status != "paid":
        _mark_payment_as_unsuccessful(
            order,
            transaction,
            provider_status=gateway_status,
            gateway_response=_format_gateway_response(payload.get("status")),
            now=now,
        )
        db.commit()
        db.refresh(order)
        return _serialize_payment_verification(
            order,
            transaction,
            provider_status=gateway_status,
            message=_unsuccessful_payment_message(
                payment_status=transaction.status,
                gateway_response=_format_gateway_response(payload.get("status")),
            ),
        )

    # Paid according to KutanaPay -- now confirm it paid what the order costs.
    # The signature on the webhook proves who sent the message, not that the
    # amount matches, and the status endpoint is simply an external system. So
    # the comparison against our own stored subunit total stays.
    paid_subunit = kutanapay_parse_amount_to_subunit(payload.get("amount"))
    if paid_subunit is None or paid_subunit != transaction.amount_subunit:
        transaction.status = "failed"
        transaction.gateway_response = "Transaction amount mismatch."
        transaction.verified_at = now
        if order.payment_status != "paid":
            order.payment_status = "failed"
        db.commit()
        db.refresh(order)
        return _serialize_payment_verification(
            order,
            transaction,
            provider_status="failed",
            message="Payment verification failed because the amount did not match the order.",
        )

    provider_payload = {
        **payload,
        # Deliberately the checkout id, not payment_reference: the webhook
        # finds this row by provider_transaction_id, and a retry arriving after
        # settlement must still match.
        "id": checkout_id,
        "gateway_response": _format_gateway_response(payload.get("status")),
        # KutanaPay does not report a processor fee on the checkout record, so
        # the ledger records zero rather than inventing a number.
        "fees": 0,
        "paid_at": payload.get("paid_at"),
        "authorization": None,
    }
    return _apply_successful_payment(db, transaction, provider_payload)


def handle_kutanapay_webhook(
    db: Session,
    *,
    raw_body: bytes,
    signature: str | None,
    event_type: str | None,
) -> dict[str, str]:
    """Handle a signed KutanaPay webhook.

    The signature is checked against the raw bytes before the body is parsed --
    re-serialising parsed JSON changes key order and whitespace and the HMAC
    then never matches.

    Even with a valid signature the event is treated as a prompt rather than as
    truth: the handler re-reads the checkout from the API and compares the
    amount. A signature establishes who sent the message, not that acting on it
    is safe.
    """
    if not verify_kutanapay_signature(raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid KutanaPay webhook signature.",
        )

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook payload.",
        ) from exc

    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    checkout_id = data.get("checkout_id") or data.get("id")
    if not checkout_id:
        # Acknowledged rather than rejected: an event we cannot act on is not a
        # delivery failure, and a non-2xx would make KutanaPay retry forever.
        return {"status": "ignored"}

    transaction = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.provider == "kutanapay",
            PaymentTransaction.provider_transaction_id == str(checkout_id),
        )
    )
    if transaction is None:
        return {"status": "unknown"}

    # Already settled. KutanaPay retries deliveries and sends an idempotency
    # key for exactly this case; re-verifying a paid transaction would be
    # harmless but pointless.
    if transaction.status == "success":
        return {"status": "success"}

    result = _verify_kutanapay_payment(db, transaction)
    return {"status": result.payment_status, "event": event_type or ""}
