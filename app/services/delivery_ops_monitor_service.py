from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.admin_controller import compute_delivery_ops_snapshot
from app.controllers.notification_controller import create_notification_event
from app.core.database import SessionLocal
from app.models import NotificationEvent, Order, User
from app.models.user import UserRole
from app.services.push_service import (
    build_push_data,
    dispatch_customer_order_push,
    send_expo_push_notification,
)

logger = logging.getLogger(__name__)

SLA_BREACH_KIND = "delivery_sla_breach"
CUSTOMER_LATE_KIND = "order_running_late"


def _already_processed(db: Session, order_id) -> bool:
    """Has this breach already been alerted on?

    This used to look for the customer's apology wallet credit, which was a
    guaranteed row because every order has a customer. That credit has been
    removed, so the admin alert itself is now the marker.

    The trade-off the old comment worried about is real but harmless: with no
    admin accounts at all, no event is written and a breached order is
    reconsidered on every pass. `process_delivery_sla_alerts` therefore returns
    before the loop when there are no admins, so that case does no work rather
    than looping over an empty recipient list forever.
    """
    return (
        db.scalar(
            select(NotificationEvent.id).where(
                NotificationEvent.kind == SLA_BREACH_KIND,
                NotificationEvent.route_target_id == str(order_id),
            )
        )
        is not None
    )


def _admin_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role == UserRole.ADMIN)).all())


def process_delivery_sla_alerts() -> None:
    db = SessionLocal()
    try:
        snapshot = compute_delivery_ops_snapshot(db)
        delayed_orders = [row for row in snapshot.orders if row.is_delayed]
        if not delayed_orders:
            return

        admins = _admin_users(db)
        if not admins:
            # Nothing can be recorded or delivered without a recipient, and
            # without a recorded event every order would be reprocessed next
            # pass anyway. Bail out instead of spinning.
            return

        for row in delayed_orders:
            if _already_processed(db, row.id):
                continue

            order = db.scalar(
                select(Order)
                .options(selectinload(Order.items), selectinload(Order.user))
                .where(Order.id == row.id)
            )
            if not order:
                continue

            try:
                stage_label = order.vendor_status.replace("_", " ")
                for admin in admins:
                    admin_event = create_notification_event(
                        db,
                        admin,
                        kind=SLA_BREACH_KIND,
                        title="Delivery running late",
                        body=(
                            f"Order #{order.order_number} has been {stage_label} for "
                            f"{row.minutes_in_stage} min — past its SLA. Check Delivery Ops."
                        ),
                        icon="alert-circle-outline",
                        accent="warning",
                        action_label="Open Delivery Ops",
                        route_type="order",
                        route_target_id=str(order.id),
                    )
                    try:
                        send_expo_push_notification(
                            user=admin,
                            title="Delivery running late",
                            body=f"Order #{order.order_number} is past SLA in '{stage_label}'.",
                            data=build_push_data(
                                push_type="delivery_sla_breach",
                                route_type="order",
                                route_target_id=str(order.id),
                                notification_event=admin_event,
                                extra={"orderId": str(order.id), "minutesInStage": row.minutes_in_stage},
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Failed to push SLA breach alert to admin %s for order %s",
                            admin.id,
                            order.id,
                        )

                _notify_customer_running_late(db, order, row.minutes_in_stage)

                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed processing SLA breach for order %s", row.id)
    finally:
        db.close()


def _notify_customer_running_late(db: Session, order: Order, minutes_in_stage: int) -> None:
    """Tell the customer their order is late. Do not pay them for it.

    An automatic GHS 5 wallet credit used to fire here, and removing it left a
    gap worth naming: the credit was also the only time anyone *told* the
    customer their order had slipped. Silence is the worse half of a late
    delivery — people chase support because nothing happened, not because of
    the money.

    So the apology stays and the payout goes. The message commits to an action
    ODOS controls (the team is now on it, support is one tap away) rather than
    an arrival time nobody can promise while the parcel is already late, and
    it never mentions compensation — raising that expectation is what turns a
    delay into a dispute.

    Fired inside the same guard as the admin alert, so a breach notifies each
    party exactly once.
    """
    user = order.user
    if not user:
        return

    waited = (
        f"{minutes_in_stage // 60}h {minutes_in_stage % 60}m"
        if minutes_in_stage >= 60
        else f"{minutes_in_stage}m"
    )

    try:
        event = create_notification_event(
            db,
            user,
            kind=CUSTOMER_LATE_KIND,
            title="Your order is running late",
            body=(
                f"Order #{order.order_number} has been in its current step for {waited}. "
                "Our team has been alerted and is chasing it now — tap to see live "
                "status or message support."
            ),
            icon="time-outline",
            accent="warning",
            action_label="Track order",
            route_type="order",
            route_target_id=str(order.id),
        )
        dispatch_customer_order_push(
            user=user,
            title="Your order is running late",
            body=(
                f"We're chasing order #{order.order_number} — our team has been alerted."
            ),
            order=order,
            notification_event=event,
        )
    except Exception:
        # A delayed order is already a bad experience; failing the whole SLA
        # sweep because one push could not be delivered would also lose the
        # admin alert that gets it fixed.
        logger.exception(
            "Failed to send running-late notice to customer for order %s", order.id
        )
