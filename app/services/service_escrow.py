"""Odos holds the money until the work is confirmed.

The customer pays in full at booking; the funds sit in platform treasury as
provider liability. Three things can release them, and nothing else:

  1. The customer confirms the work was done.
  2. The grace window elapses after the provider marked it complete, so a
     customer who simply stops replying cannot strand a provider's money.
  3. An admin releases it, resolving a dispute.

The provider marking a booking `completed` is a *claim*, not a release. That
separation is the whole point of the hold: if completion paid out directly,
the escrow would protect nobody.

Deliberately mirrors the order side (`OrderPackage.auto_release_at`,
`AUTO_RELEASE_GRACE_HOURS`) so service money behaves the way product money
already does, and finance has one mental model rather than two.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.service import Booking
from app.services.service_earnings import release_booking_earnings

#: How long the customer has to confirm or raise a problem once the provider
#: says the work is done. Matches the delivery grace window: the same promise
#: should not mean two different things depending on what was bought.
ESCROW_GRACE_HOURS = 48

#: Reasons a hold ended, recorded on the booking for finance and disputes.
RELEASE_CUSTOMER_CONFIRMED = "customer_confirmed"
RELEASE_AUTO = "auto_released"
RELEASE_ADMIN = "admin_released"


class EscrowError(RuntimeError):
    """The hold cannot be changed as asked."""


def start_hold(booking: Booking, *, now: datetime | None = None) -> None:
    """Begin the grace window. Called when the provider completes the work."""

    moment = now or datetime.now(timezone.utc)
    booking.work_completed_at = moment
    booking.auto_release_at = moment + timedelta(hours=ESCROW_GRACE_HOURS)


def confirm_and_release(
    db: Session,
    booking: Booking,
    *,
    reason: str = RELEASE_CUSTOMER_CONFIRMED,
    now: datetime | None = None,
) -> float:
    """Release the hold and pay the provider. Returns the amount credited."""

    if booking.status != "completed":
        raise EscrowError("Only completed work can be released.")
    if booking.earnings_released_at is not None:
        # Already paid -- a double confirmation, or a confirmation that
        # raced the sweeper. Neither should pay twice.
        return 0.0

    moment = now or datetime.now(timezone.utc)
    if reason == RELEASE_CUSTOMER_CONFIRMED:
        booking.customer_confirmed_at = moment

    amount = release_booking_earnings(db, booking)
    booking.earnings_release_reason = reason
    return amount


def release_due_holds(db: Session, *, now: datetime | None = None) -> int:
    """Pay out holds whose grace window has elapsed. Returns how many.

    Run on a schedule. Re-reads each booking's state immediately before
    acting so a confirmation or dispute landing in the same instant is not
    silently overwritten -- the same care `auto_release_package` takes on
    the order side.
    """

    moment = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(Booking).where(
            Booking.status == "completed",
            Booking.earnings_released_at.is_(None),
            Booking.auto_release_at.is_not(None),
            Booking.auto_release_at <= moment,
        )
    ).all()

    released = 0
    for booking in due:
        # A dispute opened after the sweep's read must stop the payout.
        if booking.status != "completed" or booking.earnings_released_at is not None:
            continue
        if confirm_and_release(db, booking, reason=RELEASE_AUTO, now=moment) >= 0:
            released += 1

    return released


def hold_summary(booking: Booking) -> dict:
    """What the two parties are shown about the hold."""

    return {
        "amount_held": (
            0.0 if booking.earnings_released_at else round(booking.amount_paid, 2)
        ),
        "work_completed_at": booking.work_completed_at,
        "auto_release_at": booking.auto_release_at,
        "customer_confirmed_at": booking.customer_confirmed_at,
        "released_at": booking.earnings_released_at,
        "release_reason": booking.earnings_release_reason,
        "awaiting_confirmation": (
            booking.status == "completed" and booking.earnings_released_at is None
        ),
    }


def process_booking_escrow_release() -> None:
    """Scheduled entry point: pay out holds whose grace window has elapsed.

    Owns its session and commits per booking, mirroring
    `process_delivery_auto_release`. One booking failing — a deleted provider,
    a locked wallet row — must not stop the rest of the queue being paid.
    """

    import logging

    from app.core.database import SessionLocal

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        moment = datetime.now(timezone.utc)
        due = db.scalars(
            select(Booking).where(
                Booking.status == "completed",
                Booking.earnings_released_at.is_(None),
                Booking.auto_release_at.is_not(None),
                Booking.auto_release_at <= moment,
            )
        ).all()

        for booking in due:
            try:
                # Re-read under the row lock: a confirmation or dispute that
                # landed since the query above must win over this sweep.
                locked = db.scalar(
                    select(Booking).where(Booking.id == booking.id).with_for_update()
                )
                if (
                    locked is None
                    or locked.status != "completed"
                    or locked.earnings_released_at is not None
                ):
                    db.rollback()
                    continue

                confirm_and_release(db, locked, reason=RELEASE_AUTO, now=moment)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed auto-releasing escrow for booking %s", booking.id
                )
    finally:
        db.close()
