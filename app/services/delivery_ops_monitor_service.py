from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.admin_controller import compute_delivery_ops_snapshot
from app.controllers.customer_wallet_controller import credit_customer_wallet_for_delivery_delay
from app.controllers.notification_controller import create_notification_event
from app.core.database import SessionLocal
from app.models import CustomerWalletTransaction, Order, User
from app.models.user import UserRole
from app.services.push_service import build_push_data, send_expo_push_notification

logger = logging.getLogger(__name__)

SLA_BREACH_KIND = "delivery_sla_breach"


def _already_processed(db: Session, order_id) -> bool:
    # The wallet credit is the source of truth for "this breach was already
    # handled" — unlike an admin NotificationEvent, it's guaranteed to exist
    # (every order has a customer) even on a day with zero admin accounts, so
    # this can never get stuck re-processing the same order forever.
    return (
        db.scalar(
            select(CustomerWalletTransaction.id).where(
                CustomerWalletTransaction.order_id == order_id,
                CustomerWalletTransaction.kind == "credit_delivery_delay",
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

                credit_customer_wallet_for_delivery_delay(db, order)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed processing SLA breach for order %s", row.id)
    finally:
        db.close()
