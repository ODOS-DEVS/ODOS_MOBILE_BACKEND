"""Telling both sides what just happened to a booking.

Rides the existing rails — `create_notification_event` for the in-app feed
(which already publishes over the realtime channel) and Expo push for the
device. No new channel, no separate inbox.

Every dispatch is wrapped so a failed notification cannot roll back the thing
it was announcing. A provider who misses a push still has a confirmed
booking; a booking that silently fails to confirm because an Expo call timed
out would be far worse.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.controllers.notification_controller import create_notification_event
from app.models import User
from app.models.service import Booking
from app.services.push_service import build_push_data, send_expo_push_notification

logger = logging.getLogger(__name__)


def _notify(
    db: Session,
    user: User | None,
    *,
    kind: str,
    title: str,
    body: str,
    icon: str,
    accent: str,
    booking: Booking,
    action_label: str | None = None,
) -> None:
    """One in-app event plus one push, neither able to break the caller."""

    if user is None:
        return

    event = None
    try:
        event = create_notification_event(
            db,
            user,
            kind=kind,
            title=title,
            body=body,
            icon=icon,
            accent=accent,
            action_label=action_label,
            route_type="booking",
            route_target_id=str(booking.id),
        )
    except Exception:
        logger.exception("Failed recording booking notification for %s", booking.id)

    try:
        send_expo_push_notification(
            user=user,
            title=title,
            body=body,
            data=build_push_data(
                push_type="booking_update",
                route_type="booking",
                route_target_id=str(booking.id),
                notification_event=event,
                extra={
                    "bookingId": str(booking.id),
                    "bookingNumber": booking.booking_number,
                    "status": booking.status,
                },
            ),
        )
    except Exception:
        logger.exception("Failed sending booking push for %s", booking.id)


def _provider(db: Session, booking: Booking) -> User | None:
    return db.get(User, booking.provider_user_id) if booking.provider_user_id else None


def _customer(db: Session, booking: Booking) -> User | None:
    return db.get(User, booking.customer_user_id) if booking.customer_user_id else None


def notify_booking_requested(db: Session, booking: Booking) -> None:
    """A customer has asked for a slot. The provider has to answer."""

    _notify(
        db,
        _provider(db, booking),
        kind="booking_request",
        title="New booking request",
        body=f"{booking.booking_number} is waiting for you to accept or decline.",
        icon="calendar-outline",
        accent="info",
        booking=booking,
        action_label="Review request",
    )


def notify_booking_accepted(db: Session, booking: Booking) -> None:
    _notify(
        db,
        _customer(db, booking),
        kind="booking_accepted",
        title="Booking accepted",
        body=f"Your provider accepted {booking.booking_number}. Pay to lock in the time.",
        icon="checkmark-circle-outline",
        accent="success",
        booking=booking,
        action_label="Pay now",
    )


def notify_booking_declined(db: Session, booking: Booking) -> None:
    _notify(
        db,
        _customer(db, booking),
        kind="booking_declined",
        title="Booking declined",
        body=f"{booking.booking_number} was declined. You have not been charged.",
        icon="close-circle-outline",
        accent="danger",
        booking=booking,
    )


def notify_booking_confirmed(db: Session, booking: Booking) -> None:
    """Paid and held. Both sides need to know the slot is real."""

    _notify(
        db,
        _customer(db, booking),
        kind="booking_confirmed",
        title="Booking confirmed",
        body=(
            f"{booking.booking_number} is confirmed. Odos is holding your "
            "payment until you confirm the work is done."
        ),
        icon="shield-checkmark-outline",
        accent="success",
        booking=booking,
        action_label="View booking",
    )
    _notify(
        db,
        _provider(db, booking),
        kind="booking_confirmed",
        title="Booking confirmed and paid",
        body=f"{booking.booking_number} is paid. Funds release once the customer confirms.",
        icon="shield-checkmark-outline",
        accent="success",
        booking=booking,
    )


def notify_work_completed(db: Session, booking: Booking) -> None:
    """The provider says it is done; the customer's confirmation pays them."""

    _notify(
        db,
        _customer(db, booking),
        kind="booking_completed",
        title="Confirm your service",
        body=(
            f"Your provider marked {booking.booking_number} complete. "
            "Confirm to release the payment."
        ),
        icon="hand-left-outline",
        accent="info",
        booking=booking,
        action_label="Confirm work",
    )


def notify_funds_released(db: Session, booking: Booking, *, amount: float) -> None:
    _notify(
        db,
        _provider(db, booking),
        kind="booking_released",
        title="Payment released",
        body=f"{amount:.2f} from {booking.booking_number} is now in your wallet.",
        icon="wallet-outline",
        accent="success",
        booking=booking,
        action_label="View wallet",
    )


def notify_booking_cancelled(db: Session, booking: Booking, *, by_role: str) -> None:
    """Tell the other party — the one who cancelled already knows."""

    recipient = _provider(db, booking) if by_role == "customer" else _customer(db, booking)
    who = "The customer" if by_role == "customer" else "The provider"
    _notify(
        db,
        recipient,
        kind="booking_cancelled",
        title="Booking cancelled",
        body=f"{who} cancelled {booking.booking_number}.",
        icon="close-circle-outline",
        accent="danger",
        booking=booking,
    )
