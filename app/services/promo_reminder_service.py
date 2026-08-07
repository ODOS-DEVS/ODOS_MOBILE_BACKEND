"""Background reminders for promotions: nudge shoppers before a claimed
voucher expires unused, and nudge vendors when their own voucher is about to
expire with zero redemptions so they can extend or retire it."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.controllers.notification_controller import create_notification_event
from app.core.database import SessionLocal
from app.models import User, Voucher, VoucherAssignment, VoucherRedemption
from app.services.push_service import build_push_data, send_expo_push_notification

logger = logging.getLogger(__name__)

SHOPPER_REMINDER_WINDOW_HOURS = 24
VENDOR_REMINDER_WINDOW_HOURS = 48


def _notify(
    db,
    user: User,
    *,
    kind: str,
    title: str,
    body: str,
    icon: str,
    route_type: str,
    route_target_id: str,
    push_type: str,
) -> None:
    notification_event = create_notification_event(
        db,
        user,
        kind=kind,
        title=title,
        body=body,
        icon=icon,
        accent="warning",
        action_label="View",
        route_type=route_type,
        route_target_id=route_target_id,
    )
    if user.expo_push_token and user.allow_notifications:
        try:
            send_expo_push_notification(
                user=user,
                title=title,
                body=body,
                data=build_push_data(
                    push_type=push_type,
                    route_type=route_type,
                    route_target_id=route_target_id,
                    notification_event=notification_event,
                ),
            )
        except Exception:
            logger.exception("Failed to push promo reminder to user %s", user.id)


def _process_shopper_voucher_expiry_reminders(db, now: datetime) -> None:
    window_end = now + timedelta(hours=SHOPPER_REMINDER_WINDOW_HOURS)
    rows = db.execute(
        select(VoucherAssignment, Voucher)
        .join(Voucher, Voucher.id == VoucherAssignment.voucher_id)
        .where(
            Voucher.is_active.is_(True),
            Voucher.ends_at.is_not(None),
            Voucher.ends_at > now,
            Voucher.ends_at <= window_end,
            VoucherAssignment.expiry_reminder_sent_at.is_(None),
        )
    ).all()

    for assignment, voucher in rows:
        already_used = db.scalar(
            select(VoucherRedemption.id).where(
                VoucherRedemption.voucher_id == voucher.id,
                VoucherRedemption.user_id == assignment.user_id,
            )
        )
        if already_used:
            assignment.expiry_reminder_sent_at = now
            db.commit()
            continue

        user = db.get(User, assignment.user_id)
        if not user:
            continue

        try:
            hours_left = max(int((voucher.ends_at - now).total_seconds() // 3600), 1)
            _notify(
                db,
                user,
                kind="voucher_expiring_soon",
                title="A saved voucher is about to expire",
                body=f"{voucher.title} ({voucher.code}) expires in about {hours_left}h — use it before it's gone.",
                icon="pricetag-outline",
                route_type="customer_vouchers",
                route_target_id=str(voucher.id),
                push_type="voucher_expiring_soon",
            )
            assignment.expiry_reminder_sent_at = now
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed processing shopper voucher expiry reminder for assignment %s",
                assignment.id,
            )


def _process_vendor_voucher_expiry_reminders(db, now: datetime) -> None:
    window_end = now + timedelta(hours=VENDOR_REMINDER_WINDOW_HOURS)
    vouchers = list(
        db.scalars(
            select(Voucher).where(
                Voucher.owner_type == "vendor",
                Voucher.is_active.is_(True),
                Voucher.approval_status == "approved",
                Voucher.ends_at.is_not(None),
                Voucher.ends_at > now,
                Voucher.ends_at <= window_end,
                Voucher.vendor_expiry_reminder_sent_at.is_(None),
                Voucher.created_by_user_id.is_not(None),
            )
        ).all()
    )

    for voucher in vouchers:
        redemption_count = db.scalar(
            select(VoucherRedemption.id).where(VoucherRedemption.voucher_id == voucher.id)
        )
        if redemption_count:
            voucher.vendor_expiry_reminder_sent_at = now
            db.commit()
            continue

        vendor = db.get(User, voucher.created_by_user_id)
        if not vendor:
            continue

        try:
            hours_left = max(int((voucher.ends_at - now).total_seconds() // 3600), 1)
            _notify(
                db,
                vendor,
                kind="vendor_voucher_expiring_unused",
                title="Your voucher expires soon with no redemptions",
                body=(
                    f"{voucher.title} ({voucher.code}) expires in about {hours_left}h and hasn't been "
                    "used yet. Extend it or promote it to shoppers before it lapses."
                ),
                icon="alert-circle-outline",
                route_type="vendor_vouchers",
                route_target_id=str(voucher.id),
                push_type="vendor_voucher_expiring_unused",
            )
            voucher.vendor_expiry_reminder_sent_at = now
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed processing vendor voucher expiry reminder for voucher %s",
                voucher.id,
            )


def process_promo_expiry_reminders() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        _process_shopper_voucher_expiry_reminders(db, now)
        _process_vendor_voucher_expiry_reminders(db, now)
    finally:
        db.close()
