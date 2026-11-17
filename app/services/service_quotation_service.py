"""The quote-first flow: describe a job, get a price, accept it.

For work that cannot be priced from a listing — rewiring a house, catering a
wedding, a custom dress. The customer describes what they need, the provider
answers with a figure, and accepting that figure raises a real booking at the
quoted price.

The quoted amount is the one number in the services domain that legitimately
originates from a human rather than from the catalogue. It is therefore
pinned the moment it is sent: the provider cannot revise it after the
customer accepts, and the customer cannot accept a figure that has expired.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.service import Booking, Service, ServiceQuotation
from app.services.finance_math import round_money
from app.services.service_booking_service import _generate_booking_number

#: How long a quote stands if the provider does not say otherwise. Long
#: enough for a customer to think and compare, short enough that a provider
#: is not held to last month's materials prices.
DEFAULT_QUOTE_VALIDITY_DAYS = 7


class QuotationError(ValueError):
    """A quotation that cannot be created or moved as asked."""


def _generate_reference(db: Session) -> str:
    for _ in range(10):
        candidate = f"QTE-{secrets.randbelow(900000) + 100000}"
        if not db.scalar(
            select(ServiceQuotation.id).where(ServiceQuotation.reference == candidate)
        ):
            return candidate
    raise QuotationError("We couldn't create a quotation reference right now.")


def request_quotation(
    db: Session,
    service: Service,
    *,
    customer_user_id,
    brief: str,
    preferred_date=None,
    location_mode: str | None = None,
    location_city: str | None = None,
) -> ServiceQuotation:
    """A customer asks what a described job would cost."""

    if service.status != "active":
        raise QuotationError("This service is not currently available.")
    if service.booking_type not in ("quote", "consultation"):
        raise QuotationError("This service is booked directly, not by quotation.")
    if not brief or not brief.strip():
        raise QuotationError("Describe what you need so the provider can price it.")

    # One open request per customer per service. Without this a customer can
    # paper the provider's inbox with the same job, and the provider cannot
    # tell which one to answer.
    existing = db.scalar(
        select(ServiceQuotation).where(
            ServiceQuotation.service_id == service.id,
            ServiceQuotation.customer_user_id == customer_user_id,
            ServiceQuotation.status.in_(("requested", "sent")),
        )
    )
    if existing is not None:
        raise QuotationError(
            "You already have an open request for this service."
        )

    quotation = ServiceQuotation(
        reference=_generate_reference(db),
        service_id=service.id,
        store_id=service.store_id,
        customer_user_id=customer_user_id,
        status="requested",
        customer_brief=brief.strip(),
        preferred_date=preferred_date,
        location_mode=location_mode,
        location_city=location_city,
        currency=service.currency,
    )
    db.add(quotation)
    db.flush()
    return quotation


def send_quotation(
    db: Session,
    quotation: ServiceQuotation,
    *,
    amount: float,
    message: str | None = None,
    estimated_duration_minutes: int | None = None,
    validity_days: int | None = None,
    now: datetime | None = None,
) -> ServiceQuotation:
    """The provider answers with a price."""

    if quotation.status not in ("requested", "sent"):
        raise QuotationError("This request can no longer be quoted.")
    if amount is None or amount < 0:
        raise QuotationError("Enter the amount you are quoting.")

    moment = now or datetime.now(timezone.utc)
    days = validity_days or DEFAULT_QUOTE_VALIDITY_DAYS

    quotation.quoted_amount = round_money(amount)
    quotation.provider_message = message
    quotation.estimated_duration_minutes = estimated_duration_minutes
    quotation.expires_at = moment + timedelta(days=days)
    quotation.status = "sent"
    return quotation


def _assert_acceptable(quotation: ServiceQuotation, now: datetime) -> None:
    if quotation.status == "accepted":
        raise QuotationError("You have already accepted this quotation.")
    if quotation.status != "sent":
        raise QuotationError("There is no quotation to accept yet.")
    if quotation.quoted_amount is None:
        raise QuotationError("This quotation has no price on it.")
    if quotation.expires_at and quotation.expires_at <= now:
        # Expired quotes are refused rather than silently honoured: the
        # provider priced a job weeks ago and may no longer be able to do it
        # for that money.
        raise QuotationError("This quotation has expired. Ask for a new one.")


def accept_quotation(
    db: Session,
    quotation: ServiceQuotation,
    *,
    start_at: datetime | None = None,
    location_address: str | None = None,
    customer_note: str | None = None,
    now: datetime | None = None,
) -> Booking:
    """Accepting turns the quote into a booking at the quoted price.

    The booking is priced from `quoted_amount`, not from the listing — that
    is the entire point of a quote. Commission is applied at the platform
    rate exactly as it is for a catalogue booking, so a provider's cut does
    not depend on how the job was priced.
    """

    moment = now or datetime.now(timezone.utc)
    _assert_acceptable(quotation, moment)

    service = db.get(Service, quotation.service_id)
    if service is None or service.status != "active":
        raise QuotationError("This service is no longer available.")

    total = round_money(quotation.quoted_amount or 0.0)
    commission_rate = float(settings.vendor_commission_rate)
    commission_amount = round_money(total * commission_rate)

    duration = quotation.estimated_duration_minutes
    scheduled_end = (
        start_at + timedelta(minutes=duration) if start_at and duration else None
    )

    booking = Booking(
        booking_number=_generate_booking_number(db),
        service_id=service.id,
        store_id=service.store_id,
        provider_user_id=service.vendor_user_id,
        customer_user_id=quotation.customer_user_id,
        # Accepted quotes always need paying: the money is then held in
        # escrow like any other booking.
        status="pending_payment",
        booking_type="quote",
        scheduled_start_at=start_at,
        scheduled_end_at=scheduled_end,
        location_mode=quotation.location_mode,
        location_address=location_address,
        location_city=quotation.location_city,
        customer_note=customer_note or quotation.customer_brief,
        currency=quotation.currency,
        base_amount=total,
        addons_amount=0.0,
        total_amount=total,
        deposit_amount=0.0,
        commission_rate=commission_rate,
        commission_amount=commission_amount,
        provider_net_amount=round_money(total - commission_amount),
        pricing_breakdown={
            "source": "quotation",
            "quotation_reference": quotation.reference,
            "quoted_amount": total,
            "commission_rate": commission_rate,
            "commission_amount": commission_amount,
        },
    )
    db.add(booking)
    db.flush()

    quotation.status = "accepted"
    quotation.booking_id = booking.id
    return booking


def decline_quotation(quotation: ServiceQuotation, *, by_provider: bool = False) -> None:
    if quotation.status in ("accepted", "declined", "withdrawn"):
        raise QuotationError("This quotation is already closed.")
    quotation.status = "withdrawn" if by_provider else "declined"


def expire_stale_quotations(db: Session, *, now: datetime | None = None) -> int:
    """Close quotes nobody acted on. Returns how many."""

    moment = now or datetime.now(timezone.utc)
    stale = db.scalars(
        select(ServiceQuotation).where(
            ServiceQuotation.status == "sent",
            ServiceQuotation.expires_at.is_not(None),
            ServiceQuotation.expires_at <= moment,
        )
    ).all()
    for quotation in stale:
        quotation.status = "expired"
    return len(stale)
