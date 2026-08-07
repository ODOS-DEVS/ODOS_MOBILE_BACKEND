"""Safety net for payments that never got a webhook and whose owner never
returned to the app to tap "refresh" — periodically re-verifies transactions
stuck in a pending state directly against Paystack so money movement doesn't
depend on either of those two things happening."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.models import CustomerWalletTopUp, PaymentTransaction
from app.services.paystack_service import verify_transaction

logger = logging.getLogger(__name__)

# Give the webhook and any client-triggered verify a grace window before we
# step in, and stop retrying after a day — a transaction still unresolved
# after that is almost certainly abandoned, not delayed.
GRACE_WINDOW_MINUTES = 10
GIVE_UP_AFTER_HOURS = 24

PENDING_ORDER_PAYMENT_STATUSES = {"initialized", "pending"}
PENDING_TOPUP_STATUSES = {"pending"}


def _process_stuck_order_payments(db, now: datetime) -> None:
    from app.controllers.payment_controller import _reconcile_payment_transaction

    cutoff_recent = now - timedelta(minutes=GRACE_WINDOW_MINUTES)
    cutoff_stale = now - timedelta(hours=GIVE_UP_AFTER_HOURS)

    transactions = list(
        db.scalars(
            select(PaymentTransaction)
            .options(selectinload(PaymentTransaction.order))
            .where(
                PaymentTransaction.status.in_(PENDING_ORDER_PAYMENT_STATUSES),
                PaymentTransaction.created_at <= cutoff_recent,
                PaymentTransaction.created_at >= cutoff_stale,
            )
        ).all()
    )

    for transaction in transactions:
        if not transaction.order:
            continue
        try:
            verification_response = verify_transaction(transaction.reference)
            provider_payload = verification_response.get("data", {})
            _reconcile_payment_transaction(db, transaction, provider_payload)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed reconciling stuck order payment %s", transaction.reference
            )


def _process_stuck_wallet_topups(db, now: datetime) -> None:
    from app.controllers.customer_wallet_controller import _reconcile_wallet_topup

    cutoff_recent = now - timedelta(minutes=GRACE_WINDOW_MINUTES)
    cutoff_stale = now - timedelta(hours=GIVE_UP_AFTER_HOURS)

    topups = list(
        db.scalars(
            select(CustomerWalletTopUp)
            .options(selectinload(CustomerWalletTopUp.wallet))
            .where(
                CustomerWalletTopUp.status.in_(PENDING_TOPUP_STATUSES),
                CustomerWalletTopUp.created_at <= cutoff_recent,
                CustomerWalletTopUp.created_at >= cutoff_stale,
            )
        ).all()
    )

    for topup in topups:
        try:
            _reconcile_wallet_topup(db, topup)
        except Exception:
            db.rollback()
            logger.exception("Failed reconciling stuck wallet top-up %s", topup.reference)


def process_stuck_payment_reconciliation() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        _process_stuck_order_payments(db, now)
        _process_stuck_wallet_topups(db, now)
    finally:
        db.close()
