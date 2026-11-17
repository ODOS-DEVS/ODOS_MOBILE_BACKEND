"""Charging for a service booking, through KutanaPay.

Deliberately a sibling of the order checkout in `payment_controller` rather
than a branch inside it. The two share the gateway, the transaction table and
the treasury, but they differ in what they are paying for and what happens
next: an order activates and ships, a booking confirms and the money is then
*held* until the work is confirmed. Threading a second meaning through the
order path would have put `if booking` in a dozen places on a live payment
route.

KutanaPay is the only provider here, named as a constant in
`service_booking_service` so no config default can reroute service money.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.controllers.finance_controller import record_booking_payment_collection
from app.core.config import settings
from app.models import User
from app.models.payment import PaymentTransaction
from app.models.service import Booking
from app.services.kutanapay_service import (
    create_checkout as kutanapay_create_checkout,
    ensure_kutanapay_configured,
    generate_reference as generate_kutanapay_reference,
)
from app.services.service_booking_service import (
    BOOKING_PAYMENT_PROVIDER,
    mark_paid,
)
from app.services.finance_math import round_money
from app.services.service_notifications import notify_booking_confirmed


def amount_to_subunit(amount: float) -> int:
    return int(round(float(amount) * 100))


def amount_from_subunit(subunit: int) -> float:
    return round_money(int(subunit) / 100)


def _load_payable_booking(db: Session, booking_id: uuid.UUID, user: User) -> Booking:
    booking = db.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None or booking.customer_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found."
        )
    if booking.status != "pending_payment":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking is not awaiting payment.",
        )
    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking has already been paid.",
        )
    if booking.total_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking has nothing to pay.",
        )
    return booking


def create_booking_checkout(
    db: Session,
    request: Request,
    current_user: User,
    booking_id: uuid.UUID,
    *,
    payment_phone: str | None = None,
    callback_url: str | None = None,
) -> dict:
    """Open a KutanaPay checkout for the full booking amount."""

    ensure_kutanapay_configured()
    booking = _load_payable_booking(db, booking_id, current_user)

    # Reuse a live checkout rather than opening a second one for the same
    # booking: two open checkouts means two ways to pay for one slot, and
    # the loser becomes a refund.
    existing = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.booking_id == booking.id,
            PaymentTransaction.status.in_(("pending", "initialized")),
        )
    )
    if existing and existing.authorization_url:
        return {
            "booking_id": booking.id,
            "booking_number": booking.booking_number,
            "reference": existing.reference,
            "authorization_url": existing.authorization_url,
            "amount": amount_from_subunit(existing.amount_subunit),
            "currency": existing.currency,
            "payment_status": booking.payment_status,
        }

    reference = generate_kutanapay_reference()

    redirect_url = (callback_url or "odosmobileexpo://payments/return")
    separator = "&" if "?" in redirect_url else "?"
    # `kind` is explicit so the landing screen can verify without guessing;
    # it may be opened on a different device from the one that paid.
    redirect_url = (
        f"{redirect_url}{separator}reference={reference}"
        f"&bookingId={booking.id}&kind=booking"
    )

    checkout = kutanapay_create_checkout(
        # Cedis. KutanaPay quotes major units; the row below stores subunits.
        amount=booking.total_amount,
        reference=reference,
        customer_email=current_user.email,
        customer_name=current_user.full_name,
        # The number the customer gave for this payment, not their profile
        # number: KutanaPay infers the mobile money network from it.
        customer_phone=payment_phone or current_user.phone_number,
        description=f"Booking {booking.booking_number}",
        order_id=str(booking.id),
        redirect_url=redirect_url,
    )

    transaction = PaymentTransaction(
        booking_id=booking.id,
        order_id=None,
        user_id=current_user.id,
        provider=BOOKING_PAYMENT_PROVIDER,
        reference=reference,
        authorization_url=checkout["checkout_url"],
        currency=settings.kutanapay_currency,
        amount_subunit=amount_to_subunit(booking.total_amount),
        status="pending",
        # Always the whole price: bookings are never part-paid.
        purpose="full",
        provider_transaction_id=str(checkout["id"]) if checkout.get("id") else None,
        authorization_data={
            "payment_reference": checkout.get("payment_reference"),
            "expires_at": checkout.get("expires_at"),
        },
    )
    db.add(transaction)
    db.commit()

    return {
        "booking_id": booking.id,
        "booking_number": booking.booking_number,
        "reference": reference,
        "authorization_url": checkout["checkout_url"],
        "amount": booking.total_amount,
        "currency": settings.kutanapay_currency,
        "payment_status": booking.payment_status,
    }


def apply_verified_booking_payment(
    db: Session,
    transaction: PaymentTransaction,
    provider_payload: dict,
) -> Booking | None:
    """Settle a booking payment the backend has already verified.

    The single place a booking becomes paid. Never called from a client
    claim of success — only from gateway verification or a signed webhook.

    Idempotent: `payment_status` is checked under a row lock, so a webhook
    delivered twice, or a webhook racing the customer's return to the app,
    confirms and books the money exactly once.
    """

    if transaction.booking_id is None:
        return None

    booking = db.scalar(
        select(Booking).where(Booking.id == transaction.booking_id).with_for_update()
    )
    if booking is None:
        return None

    now = datetime.now(UTC)
    transaction.status = "paid"
    transaction.gateway_response = provider_payload.get("gateway_response")
    transaction.raw_response = provider_payload
    transaction.paid_at = transaction.paid_at or now
    transaction.verified_at = now
    transaction.last_checked_at = now

    if booking.payment_status == "paid":
        # Already settled by whichever caller arrived first.
        db.commit()
        return booking

    amount = amount_from_subunit(transaction.amount_subunit)

    # Books the money into treasury as provider liability. It is *held*
    # there: the provider is not credited until the work is confirmed.
    record_booking_payment_collection(
        db,
        booking,
        transaction,
        processor_fee_amount=amount_from_subunit(transaction.processor_fee_subunit or 0),
    )

    # Confirms the booking and locks in the slot.
    mark_paid(db, booking, amount=amount, purpose="full")

    db.commit()
    db.refresh(booking)

    # Only on the delivery that actually settled the payment. A retried
    # webhook returns early above, so this cannot notify twice.
    if booking.status == "confirmed":
        notify_booking_confirmed(db, booking)
        db.commit()

    return booking


def verify_booking_payment(db: Session, transaction: PaymentTransaction) -> dict:
    """Ask KutanaPay what happened to a booking payment and act on the answer.

    The booking counterpart of `_verify_kutanapay_payment`. Shared by the
    webhook and by the app's own verify call, so the payment is judged the
    same way whichever arrives first — and they routinely race.
    """

    from app.services.kutanapay_service import (
        get_checkout as kutanapay_get_checkout,
        normalize_status as kutanapay_normalize_status,
    )

    checkout_id = transaction.provider_transaction_id
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
        # The slot is not held by a failed payment. The booking stays in
        # pending_payment until its deadline, so the customer can retry
        # without losing the time they chose.
        transaction.status = (
            "failed" if gateway_status in ("failed", "cancelled") else "pending"
        )
        db.commit()
        return {
            "payment_status": gateway_status,
            "booking_id": str(transaction.booking_id),
            "paid": False,
        }

    booking = apply_verified_booking_payment(db, transaction, payload)
    return {
        "payment_status": "paid",
        "booking_id": str(transaction.booking_id),
        "booking_status": booking.status if booking else None,
        "paid": True,
    }


def verify_booking_payment_for_user(
    db: Session, reference: str, current_user: User
) -> dict:
    """Verify by reference, for the app's return-from-gateway screen."""

    transaction = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.reference == reference,
            PaymentTransaction.user_id == current_user.id,
            PaymentTransaction.booking_id.is_not(None),
        )
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That payment session was not found.",
        )
    return verify_booking_payment(db, transaction)
