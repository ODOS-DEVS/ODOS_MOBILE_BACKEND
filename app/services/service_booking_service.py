"""Creating and moving bookings.

The one place the four engines meet: `service_pricing` says what it costs,
`service_availability` says whether the time is free, `service_booking_state`
says who may change what, and this module writes the result down and records
why.

Everything a customer sends is treated as a *request*: which service, which
package, which extras, when. Prices, durations and commission are
read from the database and computed here. Nothing monetary crosses the wire
inbound.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.service import (
    Booking,
    BookingAddon,
    BookingStatusEvent,
    Service,
)
from app.services.service_availability import (
    AvailabilityError,
    assert_slot_bookable,
)
from app.services.service_booking_state import assert_transition
from app.services.service_pricing import (
    AddonSelection,
    PriceQuote,
    PricingError,
    quote_booking,
)

#: The only payment provider for service bookings.
#:
#: Named here rather than read from a per-request default so that a change to
#: the order-side default can never silently route booking money through a
#: different processor. KutanaPay is the platform's live collections provider;
#: there is no second gateway to fall back to by design.
BOOKING_PAYMENT_PROVIDER = "kutanapay"

#: How long a customer has to pay before the slot goes back on sale. Long
#: enough to finish a mobile-money prompt on a poor connection, short enough
#: that an abandoned checkout does not hold a Saturday morning hostage.
PAYMENT_WINDOW_MINUTES = 20


class BookingError(ValueError):
    """A booking that cannot be created or moved as asked."""


class SlotTakenError(BookingError):
    """Lost the race for a slot. Distinct so callers can offer alternatives."""


@dataclass(frozen=True)
class BookingRequest:
    """What the customer asked for. No amounts — those are computed."""

    service_id: str
    customer_user_id: object
    package_id: str | None = None
    addons: tuple[AddonSelection, ...] = ()
    hours: float | None = None
    start_at: datetime | None = None
    location_mode: str | None = None
    location_address: str | None = None
    location_city: str | None = None
    customer_note: str | None = None


def _generate_booking_number(db: Session) -> str:
    # Mirrors `_generate_order_number`: a short human reference, retried on
    # the unlikely collision rather than coordinated through a sequence.
    for _ in range(10):
        candidate = f"BKG-{secrets.randbelow(900000) + 100000}"
        if not db.scalar(select(Booking.id).where(Booking.booking_number == candidate)):
            return candidate
    raise BookingError("We couldn't create a booking reference right now.")


def _initial_status(service: Service, quote: PriceQuote) -> str:
    """Where a new booking starts, given how the service is sold."""

    if service.booking_type == "request":
        # The provider has to agree before anything else happens, including
        # payment -- asking for money before acceptance is how refunds start.
        return "pending_provider"
    if service.booking_type == "instant":
        return "pending_payment" if quote.requires_payment_to_confirm else "confirmed"
    # Quote and consultation bookings are raised elsewhere, from an accepted
    # quotation; they never begin life as a direct booking.
    raise BookingError("This service is not booked directly.")


def _validate_location(service: Service, request: BookingRequest) -> None:
    modes = service.location_modes or []
    if not modes:
        return
    if request.location_mode is None:
        raise BookingError("Choose where the service should happen.")
    if request.location_mode not in modes:
        raise BookingError("This provider does not offer that location option.")
    # Only ask for an address when someone has to travel to one. An online
    # tutor has no use for the customer's street.
    if request.location_mode == "customer" and not (request.location_address or "").strip():
        raise BookingError("Add the address the provider should come to.")


def record_status_change(
    db: Session,
    booking: Booking,
    *,
    to_status: str,
    actor_role: str,
    actor_user_id=None,
    note: str | None = None,
    metadata: dict | None = None,
) -> BookingStatusEvent:
    """Move a booking, enforcing the state machine, and log who did it."""

    assert_transition(booking.status, to_status, actor_role=actor_role)

    event = BookingStatusEvent(
        booking_id=booking.id,
        from_status=booking.status,
        to_status=to_status,
        actor_role=actor_role,
        actor_user_id=actor_user_id,
        note=note,
        metadata_json=metadata,
    )
    db.add(event)

    now = datetime.now(timezone.utc)
    booking.status = to_status
    if to_status == "confirmed":
        booking.confirmed_at = now
        # The slot is held on its own merit now, not by a payment timer.
        booking.payment_deadline_at = None
    elif to_status == "in_progress":
        booking.started_at = now
    elif to_status == "completed":
        booking.completed_at = now
    elif to_status in ("cancelled", "declined", "expired"):
        booking.cancelled_at = now
        booking.cancelled_by = actor_role
        if note:
            booking.cancellation_reason = note[:500]

    return event


def create_booking(
    db: Session,
    service: Service,
    request: BookingRequest,
    *,
    now: datetime | None = None,
) -> Booking:
    """Price, validate and write a booking. Caller commits."""

    if service.status != "active":
        raise BookingError("This service is not currently bookable.")
    if service.id != request.service_id:
        raise BookingError("Service mismatch.")

    try:
        quote = quote_booking(
            service,
            package_id=request.package_id,
            addons=list(request.addons),
            hours=request.hours,
        )
    except PricingError as exc:
        raise BookingError(str(exc)) from exc

    _validate_location(service, request)

    scheduled_end = None
    if quote.duration_minutes > 0:
        if request.start_at is None:
            raise BookingError("Choose a date and time.")
        try:
            scheduled_end = assert_slot_bookable(
                db,
                service,
                start_at=request.start_at,
                duration_minutes=quote.duration_minutes,
                now=now,
            )
        except AvailabilityError as exc:
            raise BookingError(str(exc)) from exc

    status = _initial_status(service, quote)

    booking = Booking(
        booking_number=_generate_booking_number(db),
        service_id=service.id,
        store_id=service.store_id,
        provider_user_id=service.vendor_user_id,
        customer_user_id=request.customer_user_id,
        package_id=request.package_id,
        status=status,
        booking_type=service.booking_type,
        scheduled_start_at=request.start_at,
        scheduled_end_at=scheduled_end,
        location_mode=request.location_mode,
        location_address=request.location_address,
        location_city=request.location_city,
        customer_note=request.customer_note,
        currency=quote.currency,
        base_amount=quote.base_amount,
        addons_amount=quote.addons_amount,
        total_amount=quote.total_amount,
        commission_rate=quote.commission_rate,
        commission_amount=quote.commission_amount,
        provider_net_amount=quote.provider_net_amount,
        pricing_breakdown=quote.breakdown,
        payment_deadline_at=(
            (now or datetime.now(timezone.utc)) + timedelta(minutes=PAYMENT_WINDOW_MINUTES)
            if status == "pending_payment"
            else None
        ),
    )
    db.add(booking)
    db.flush()  # surface the overlap constraint here, not at commit

    for priced in quote.addons:
        db.add(
            BookingAddon(
                booking_id=booking.id,
                addon_id=priced.addon_id,
                title=priced.title,
                unit_price=priced.unit_price,
                quantity=priced.quantity,
                duration_minutes=priced.duration_minutes,
                line_total=priced.line_total,
            )
        )

    db.add(
        BookingStatusEvent(
            booking_id=booking.id,
            from_status=None,
            to_status=status,
            actor_role="customer",
            actor_user_id=request.customer_user_id,
            note="Booking created",
        )
    )

    return booking


def create_booking_safely(
    db: Session,
    service: Service,
    request: BookingRequest,
    *,
    now: datetime | None = None,
) -> Booking:
    """`create_booking`, translating a lost slot race into a clear error.

    The availability check and the insert cannot be atomic, so two customers
    can both pass the check. The database rejects the second via
    `ix_bookings_no_overlap`; this turns that into something a UI can say out
    loud instead of a 500.
    """

    try:
        return create_booking(db, service, request, now=now)
    except IntegrityError as exc:
        db.rollback()
        if "ix_bookings_no_overlap" in str(exc.orig):
            raise SlotTakenError(
                "Someone just booked that time. Please choose another slot."
            ) from exc
        raise


def mark_paid(
    db: Session,
    booking: Booking,
    *,
    amount: float,
    purpose: str = "full",
) -> None:
    """Apply a verified payment. Only ever called after backend verification.

    A booking is paid in full or not at all, so there is no partial state to
    track: the money arrives once, Odos holds it, and the booking confirms.
    The customer's protection comes from the hold, not from withholding part
    of the price -- see `service_escrow`.

    Idempotent against a replayed webhook: a booking already past
    `pending_payment` is not advanced again.
    """

    booking.amount_paid = round(booking.amount_paid + amount, 2)

    # A tolerance of a pesewa absorbs the rounding a gateway applies when it
    # converts to and from subunits; without it an exact payment can look
    # a fraction short and leave the booking stuck unconfirmed.
    if booking.amount_paid + 0.01 >= booking.total_amount:
        booking.payment_status = "paid"

    if (
        booking.status == "pending_payment"
        and booking.payment_status == "paid"
    ):
        record_status_change(
            db,
            booking,
            to_status="confirmed",
            actor_role="system",
            note="Payment verified and held by Odos",
            metadata={"amount": amount, "purpose": purpose},
        )


def expire_stale_bookings(db: Session, *, now: datetime | None = None) -> int:
    """Release slots held by unpaid bookings past their deadline.

    Opening a booking screen must not reserve a slot indefinitely; this is
    what makes `payment_deadline_at` mean anything. Returns how many were
    expired so a scheduled task can log it.
    """

    moment = now or datetime.now(timezone.utc)
    stale = db.scalars(
        select(Booking).where(
            Booking.status == "pending_payment",
            Booking.payment_deadline_at.is_not(None),
            Booking.payment_deadline_at < moment,
        )
    ).all()

    for booking in stale:
        # A customer who paid between the sweep's read and now keeps the
        # slot: money already taken outranks the timer.
        if booking.amount_paid > 0:
            continue
        record_status_change(
            db,
            booking,
            to_status="expired",
            actor_role="system",
            note="Payment window elapsed",
        )

    return len(stale)
