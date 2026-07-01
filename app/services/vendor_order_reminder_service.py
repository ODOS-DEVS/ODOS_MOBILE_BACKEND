from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event, order_notification_image
from app.controllers.vendor_controller import VENDOR_ACTIVE_ORDER_STATUSES, list_vendor_orders_payloads
from app.core.database import SessionLocal
from app.models import NotificationEvent, Order, User
from app.models.user import UserRole, VendorStatus
from app.services.push_service import build_push_data, send_vendor_order_push

logger = logging.getLogger(__name__)

REMINDER_TIERS_MINUTES = (5, 15, 30, 45)


def _reminder_kind(minutes: int) -> str:
    return f"vendor_order_reminder_{minutes}"


def _reminder_already_sent(db: Session, *, user_id, order_id, minutes: int) -> bool:
    return (
        db.scalar(
            select(NotificationEvent.id).where(
                NotificationEvent.user_id == user_id,
                NotificationEvent.kind == _reminder_kind(minutes),
                NotificationEvent.route_target_id == str(order_id),
            )
        )
        is not None
    )


def _order_anchor_time(order: Order) -> datetime:
    anchor = order.paid_at or order.placed_at or order.created_at
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=UTC)
    return anchor.astimezone(UTC)


def process_vendor_order_reminders() -> None:
    db = SessionLocal()
    try:
        vendors = list(
            db.scalars(
                select(User).where(
                    User.vendor_status == VendorStatus.APPROVED,
                    User.allow_notifications.is_(True),
                    User.vendor_order_notifications.is_(True),
                )
            ).all()
        )

        now = datetime.now(UTC)

        for vendor in vendors:
            if UserRole.VENDOR.value not in vendor.roles:
                continue

            vendor_orders = [
                order
                for order in list_vendor_orders_payloads(db, vendor)
                if order.status in VENDOR_ACTIVE_ORDER_STATUSES
            ]

            for vendor_order in vendor_orders:
                order = db.scalar(
                    select(Order)
                    .options(selectinload(Order.items))
                    .where(Order.id == vendor_order.id)
                )
                if not order:
                    continue

                if db.scalar(
                    select(NotificationEvent.id).where(
                        NotificationEvent.user_id == vendor.id,
                        NotificationEvent.kind == "vendor_order_acknowledged",
                        NotificationEvent.route_target_id == str(order.id),
                    )
                ):
                    continue

                age_minutes = (now - _order_anchor_time(order)).total_seconds() / 60
                due_tiers = [
                    minutes
                    for minutes in REMINDER_TIERS_MINUTES
                    if age_minutes >= minutes
                    and not _reminder_already_sent(
                        db,
                        user_id=vendor.id,
                        order_id=order.id,
                        minutes=minutes,
                    )
                ]
                if not due_tiers:
                    continue

                minutes = due_tiers[-1]
                preview = order_notification_image(order)
                item_label = "item" if vendor_order.product_count == 1 else "items"
                amount_label = f"GHS {vendor_order.total_amount:,.2f}"
                urgency = "Still waiting" if minutes < 30 else "Urgent"
                notification_body = (
                    f"{urgency}: Order #{order.order_number} has been waiting {minutes} minutes. "
                    f"{vendor_order.product_count} {item_label} · {amount_label}."
                )

                try:
                    reminder_event = create_notification_event(
                        db,
                        vendor,
                        kind=_reminder_kind(minutes),
                        title=f"Order reminder · {minutes} min",
                        body=notification_body,
                        icon="receipt-outline",
                        accent="warning",
                        action_label="Open orders",
                        route_type="vendor_order",
                        route_target_id=str(order.id),
                        image_key=preview["image_key"],
                        image_url=preview["image_url"],
                    )
                    send_vendor_order_push(
                        user=vendor,
                        title=f"Order #{order.order_number} still pending",
                        body=f"{minutes} min waiting · {vendor_order.product_count} {item_label} · {amount_label}",
                        data=build_push_data(
                            push_type="vendor_order_reminder",
                            route_type="vendor_order",
                            route_target_id=str(order.id),
                            notification_event=reminder_event,
                            extra={
                                "orderId": str(order.id),
                                "orderNumber": order.order_number,
                                "productCount": vendor_order.product_count,
                                "totalAmount": vendor_order.total_amount,
                                "reminderMinutes": minutes,
                                "alertKind": "reminder",
                            },
                        ),
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception(
                        "Failed vendor order reminder (%s min) for order %s vendor %s",
                        minutes,
                        order.id,
                        vendor.id,
                    )
    finally:
        db.close()
