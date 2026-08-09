"""Background loop for the delivery auto-release grace window.

All the actual state-transition/eligibility/settlement logic lives in
delivery_lifecycle_service (auto_release_delivery re-validates eligibility
under a row lock immediately before acting) — this module only owns finding
which orders are due for a reminder or a release, using the indexed
auto_release_at column set at dispatch time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.models import Order
from app.services.delivery_lifecycle_service import (
    AUTO_RELEASE_GRACE_HOURS,
    AUTO_RELEASE_REMINDER_HOURS,
    OUT_FOR_DELIVERY,
    auto_release_delivery,
    send_auto_release_reminder,
)

logger = logging.getLogger(__name__)

REMINDER_LEAD_HOURS = AUTO_RELEASE_GRACE_HOURS - AUTO_RELEASE_REMINDER_HOURS


def _release_candidate_ids(db, now: datetime) -> list:
    return list(
        db.scalars(
            select(Order.id).where(
                Order.delivery_status == OUT_FOR_DELIVERY,
                Order.auto_release_at.is_not(None),
                Order.auto_release_at <= now,
            )
        ).all()
    )


def _reminder_candidates(db, now: datetime) -> list[Order]:
    reminder_deadline = now + timedelta(hours=REMINDER_LEAD_HOURS)
    return list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.user))
            .where(
                Order.delivery_status == OUT_FOR_DELIVERY,
                Order.auto_release_at.is_not(None),
                Order.auto_release_at > now,
                Order.auto_release_at <= reminder_deadline,
                Order.delivery_reminder_sent_at.is_(None),
            )
        ).all()
    )


def process_delivery_auto_release() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        for order_id in _release_candidate_ids(db, now):
            try:
                auto_release_delivery(db, order_id)
            except Exception:
                db.rollback()
                logger.exception("Failed processing delivery auto-release for order %s", order_id)

        for order in _reminder_candidates(db, now):
            if not order.user:
                continue
            try:
                send_auto_release_reminder(db, order)
            except Exception:
                db.rollback()
                logger.exception("Failed sending auto-release reminder for order %s", order.id)
    finally:
        db.close()
