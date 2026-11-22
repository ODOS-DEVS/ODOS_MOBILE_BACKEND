"""Close out orders nobody paid for, and finish cancellations left in limbo.

Two jobs that run together because they are the same problem seen from either
end: an order whose payment never resolved. One lapses it and puts the stock
back; the other finishes a cancellation that was held waiting for the provider
to speak.

Both are idempotent and both work in bounded batches. The sweep is driven by a
partial index on `(status, created_at) WHERE status = 'pending_payment'`, so it
touches the handful of unpaid orders rather than scanning the table.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Order, Product, User
from app.models.payment import PaymentTransaction
from app.services import order_state_service as st
from app.services.inventory_service import record_stock_change
from app.services.order_timeline_service import record_order_status_event

logger = logging.getLogger(__name__)

ORDER_EXPIRY_INTERVAL_SECONDS = 300

#: Never sweep more than this in one pass, so a backlog cannot turn into a
#: single enormous transaction.
BATCH_LIMIT = 200


def _notify(db: Session, order: Order, *, kind: str, title: str, body: str) -> None:
    from app.controllers.notification_controller import create_notification_event

    user = db.get(User, order.user_id)
    if not user:
        return
    try:
        create_notification_event(
            db,
            user,
            kind=kind,
            title=title,
            body=body,
            icon="time-outline",
            accent="warning",
            action_label="View order",
            route_type="order",
            route_target_id=str(order.id),
        )
    except Exception:
        logger.exception("Failed to notify %s about order %s", order.user_id, order.id)


def _release_stock(db: Session, order: Order) -> None:
    """Put reserved units back.

    Only for orders that never left the vendor. An order being swept here is
    unpaid and therefore was never dispatched, but the check costs nothing and
    protects against a future caller with looser assumptions.
    """
    if st.vendor_stage(order) in st.AFTER_DISPATCH:
        return
    for item in order.items:
        if not item.product_id:
            continue
        product = db.get(Product, item.product_id)
        if product is not None:
            record_stock_change(
                db,
                product,
                new_stock=int(product.stock or 0) + int(item.quantity or 0),
                reason="system",
                reference_type="order",
                reference_id=str(order.id),
                note=f"Order #{order.order_number} expired unpaid",
            )


def expire_unpaid_orders(db: Session | None = None) -> int:
    """Lapse unpaid orders past the payment window. Returns how many moved.

    An order is only swept when its payment transaction is *not* still pending.
    A mobile-money prompt sitting unanswered on someone's phone is slow, not
    abandoned, and tearing the order away under it would either strand their
    money or leave them paying for something that no longer exists. Those are
    left for the reconciliation job, which asks the provider directly.
    """
    owns_session = db is None
    db = db or SessionLocal()
    try:
        window = timedelta(minutes=max(1, int(settings.order_payment_window_minutes)))
        cutoff = datetime.now(UTC) - window

        candidates = list(
            db.scalars(
                select(Order)
                .options(selectinload(Order.items))
                .where(
                    Order.status == st.ORDER_PENDING_PAYMENT,
                    Order.created_at <= cutoff,
                )
                .limit(BATCH_LIMIT)
            ).all()
        )

        expired = 0
        for order in candidates:
            still_trying = db.scalar(
                select(PaymentTransaction.id).where(
                    PaymentTransaction.order_id == order.id,
                    PaymentTransaction.status == "pending",
                )
            )
            if still_trying is not None:
                continue

            # Conditional update: another worker, or a payment landing this
            # instant, must not find the order swept out from under it.
            moved = db.execute(
                update(Order)
                .where(Order.id == order.id, Order.status == st.ORDER_PENDING_PAYMENT)
                .values(
                    status=st.ORDER_EXPIRED,
                    payment_status=st.PAYMENT_EXPIRED,
                    progress=0,
                    tracking_eta=None,
                )
            )
            if moved.rowcount != 1:
                continue

            _release_stock(db, order)
            record_order_status_event(
                db,
                order,
                status="payment_expired",
                actor_role="system",
                note="Payment was not completed within the allowed window",
            )
            db.commit()

            _notify(
                db,
                order,
                kind="order_payment_expired",
                title="Order expired",
                body=(
                    f"Order #{order.order_number} expired because payment wasn't completed. "
                    "Nothing was charged — you can order again any time."
                ),
            )
            db.commit()
            expired += 1

        if expired:
            logger.info("Expired %s unpaid orders", expired)
        return expired
    finally:
        if owns_session:
            db.close()


def resolve_held_cancellations(db: Session | None = None) -> int:
    """Finish cancellations that were waiting on the payment provider.

    These are the requests taken while a payment was in flight. Once the
    transaction reaches a definite state the answer follows from it: a failed
    payment means cancel with nothing to refund, a successful one means cancel
    and open a refund. Until then the request stays held, which is the honest
    position rather than a guess.
    """
    owns_session = db is None
    db = db or SessionLocal()
    try:
        from app.controllers.order_cancellation_controller import _finalise_cancellation

        held = list(
            db.scalars(
                select(Order)
                .options(selectinload(Order.items))
                .where(
                    Order.cancellation_status == st.CANCEL_REQUESTED,
                    Order.status == st.ORDER_PENDING_PAYMENT,
                )
                .limit(BATCH_LIMIT)
            ).all()
        )

        resolved = 0
        for order in held:
            txn = db.scalar(
                select(PaymentTransaction).where(PaymentTransaction.order_id == order.id)
            )
            if txn is not None and txn.status == "pending":
                continue  # provider still has not spoken

            actor = db.get(User, order.cancellation_requested_by) if order.cancellation_requested_by else None
            if actor is None:
                actor = db.get(User, order.user_id)
            if actor is None:
                continue

            # Mirror whatever the provider settled on before closing the order,
            # so the record says what actually happened to the money.
            if txn is not None and txn.status == "paid":
                order.payment_status = st.PAYMENT_PAID
            elif txn is not None:
                order.payment_status = st.PAYMENT_FAILED
            db.flush()

            _finalise_cancellation(db, order, actor_role="system", decided_by=actor)
            resolved += 1

        if resolved:
            logger.info("Resolved %s held cancellations", resolved)
        return resolved
    finally:
        if owns_session:
            db.close()


def process_order_lifecycle_maintenance() -> None:
    """The scheduled entry point for both sweeps."""
    expire_unpaid_orders()
    resolve_held_cancellations()
