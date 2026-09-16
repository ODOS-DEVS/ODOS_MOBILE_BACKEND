"""Background loop for the delivery auto-release grace window.

All the actual state-transition/eligibility/settlement logic lives in
delivery_lifecycle_service (auto_release_package re-validates eligibility
under a row lock immediately before acting) — this module only owns finding
which packages are due for a reminder or a release, using the indexed
auto_release_at column set at dispatch time.

The sweep scans **packages**, not orders. Each vendor's bag is dispatched on
its own schedule and so carries its own 48-hour clock: a customer who gets the
dress on Monday and the sneakers on Thursday has a full grace window to speak
up about each, and the shop that delivered on time is paid on time rather than
waiting on the shop that didn't.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.models import Order, OrderPackage
from app.services.delivery_lifecycle_service import (
    AUTO_RELEASE_GRACE_HOURS,
    AUTO_RELEASE_REMINDER_HOURS,
    OUT_FOR_DELIVERY,
    auto_release_package,
    send_auto_release_reminder,
)

logger = logging.getLogger(__name__)

REMINDER_LEAD_HOURS = AUTO_RELEASE_GRACE_HOURS - AUTO_RELEASE_REMINDER_HOURS


def _release_candidates(db, now: datetime) -> list[tuple]:
    """(order_id, package_id) pairs whose grace window has elapsed.

    Ids only, not rows: `auto_release_package` locks and re-reads each one
    anyway, so loading full objects here would only widen the window between
    "looked eligible" and "actually acted".
    """
    return list(
        db.execute(
            select(OrderPackage.order_id, OrderPackage.id).where(
                OrderPackage.delivery_status == OUT_FOR_DELIVERY,
                OrderPackage.auto_release_at.is_not(None),
                OrderPackage.auto_release_at <= now,
            )
        ).all()
    )


def _reminder_candidates(db, now: datetime) -> list[OrderPackage]:
    reminder_deadline = now + timedelta(hours=REMINDER_LEAD_HOURS)
    return list(
        db.scalars(
            select(OrderPackage)
            .options(
                selectinload(OrderPackage.order).selectinload(Order.user),
                selectinload(OrderPackage.order).selectinload(Order.packages),
            )
            .where(
                OrderPackage.delivery_status == OUT_FOR_DELIVERY,
                OrderPackage.auto_release_at.is_not(None),
                OrderPackage.auto_release_at > now,
                OrderPackage.auto_release_at <= reminder_deadline,
                OrderPackage.delivery_reminder_sent_at.is_(None),
            )
        ).all()
    )


def process_delivery_auto_release() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        for order_id, package_id in _release_candidates(db, now):
            try:
                auto_release_package(db, order_id, package_id)
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed processing delivery auto-release for package %s (order %s)",
                    package_id,
                    order_id,
                )

        for package in _reminder_candidates(db, now):
            order = package.order
            if not order or not order.user:
                continue
            try:
                send_auto_release_reminder(db, order, package)
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed sending auto-release reminder for package %s (order %s)",
                    package.id,
                    order.id,
                )
    finally:
        db.close()
