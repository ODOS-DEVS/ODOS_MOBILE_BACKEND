"""Safety net for payments that never got a webhook and whose owner never
returned to the app to tap "refresh" — periodically re-verifies transactions
stuck in a pending state directly against the gateway that took them, so money
movement doesn't depend on either of those two things happening.

Each transaction is verified against **its own** provider. That sounds obvious
and was briefly not the case: every pending row was sent to Paystack's
``/transaction/verify``, including KutanaPay rows keyed on an ``odos-<uuid>``
reference Paystack has never heard of. Paystack answered ``status: false``,
``_parse_json_response`` raised, and the exception handler below swallowed it —
so nothing was corrupted, but KutanaPay payments had no safety net at all. A
missed webhook left a customer charged and their order inactive until they
happened to reopen the app, and the loop logged a traceback for that row every
five minutes for a day.

Providers we do not recognise are skipped rather than guessed at. Rows written
while iPay was the collector are still in the table; asking any current gateway
about them returns "not found", which would be read as a failure and would tell
a customer a completed payment had not worked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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

# Providers this job knows how to ask. Anything else — notably the retired
# "ipay" rows — is left exactly as recorded.
RECONCILABLE_PROVIDERS = frozenset({"kutanapay", "paystack"})


def _process_stuck_order_payments(db, now: datetime) -> None:
    # Imported here, not at module scope: payment_controller imports this
    # module's sibling services and a top-level import closes the cycle.
    from app.controllers.payment_controller import (
        _reconcile_payment_transaction,
        _verify_kutanapay_payment,
    )

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
        provider = (transaction.provider or "").strip().lower()
        if provider not in RECONCILABLE_PROVIDERS:
            continue
        try:
            if provider == "kutanapay":
                # Already does its own amount check, commit and status mapping,
                # and is the same path the webhook and the app's verify take —
                # so a payment is judged identically however it is noticed.
                _verify_kutanapay_payment(db, transaction)
            else:
                verification_response = verify_transaction(transaction.reference)
                provider_payload = verification_response.get("data", {})
                _reconcile_payment_transaction(db, transaction, provider_payload)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed reconciling stuck %s order payment %s",
                provider,
                transaction.reference,
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
        now = datetime.now(UTC)
        _process_stuck_order_payments(db, now)
        _process_stuck_wallet_topups(db, now)
    finally:
        db.close()
