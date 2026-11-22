"""Cancelling an order, and getting the money back.

Two processes, deliberately not one. Cancelling stops fulfilment; refunding
returns money. They happen at different moments, can fail independently, and
collapsing them is what produced orders whose payment was recorded as
"cancelled" — a word that answers neither question.

Nothing here decides policy on its own; `order_state_service` holds the rules
and this module carries them out inside transactions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.models import Order, Product, User
from app.models.order_refund import OrderRefund
from app.services import order_state_service as st
from app.services.inventory_service import record_stock_change
from app.services.order_timeline_service import record_order_status_event
from app.services.refund_service import (
    open_refund_for,
    quote_refund,
    refund_reference,
    refunded_total,
)

logger = logging.getLogger(__name__)

#: Structured reasons, so cancellations can be counted rather than only read.
CUSTOMER_REASONS = {
    "ordered_by_mistake",
    "found_better_price",
    "changed_my_mind",
    "wrong_product",
    "wrong_delivery_details",
    "taking_too_long",
    "duplicate_order",
    "other",
}
VENDOR_REASONS = {"out_of_stock", "product_unavailable", "listing_error", "cannot_fulfil", "other"}
ADMIN_REASONS = {"payment_concern", "policy_violation", "vendor_issue", "customer_request", "system_error", "other"}


def _load(db: Session, order_id: str) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.packages))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That order was not found.")
    return order


def _notify(db: Session, user_id, *, order: Order, kind: str, title: str, body: str, accent="neutral"):
    recipient = db.get(User, user_id) if user_id else None
    if not recipient:
        return
    try:
        create_notification_event(
            db,
            recipient,
            kind=kind,
            title=title,
            body=body,
            icon="close-circle-outline",
            accent=accent,
            action_label="View order",
            route_type="order",
            route_target_id=str(order.id),
        )
    except Exception:
        logger.exception("Failed to record cancellation notification for %s", user_id)


def _has_open_payment_attempt(db: Session, order: Order) -> bool:
    """Is a payment for this order still with the provider?

    This is what separates "walked away from checkout" from "the mobile-money
    prompt is still on their phone". The first can be cancelled outright; the
    second must be held, because tearing the order away mid-payment either
    strands the money or hands over free goods.
    """
    from app.models.payment import PaymentTransaction

    return (
        db.scalar(
            select(PaymentTransaction.id).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.status == "pending",
            )
        )
        is not None
    )


def _order_vendor_ids(db: Session, order: Order) -> list[uuid.UUID]:
    ids = {item.vendor_user_id for item in order.items if item.vendor_user_id}
    if ids:
        return list(ids)
    rows = db.scalars(
        select(Product.vendor_user_id).where(
            Product.id.in_([i.product_id for i in order.items if i.product_id]),
            Product.vendor_user_id.is_not(None),
        )
    ).all()
    return list({r for r in rows if r})


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------


def request_cancellation(
    db: Session,
    user: User,
    order_id: str,
    *,
    reason_code: str,
    note: str | None = None,
    actor_role: str = "customer",
) -> Order:
    """Ask to cancel. Finishes immediately when policy allows, else waits.

    The distinction matters to the shopper: "cancelled" and "we've asked the
    seller" are different promises, and showing the first when the second is
    true is how people end up believing a parcel is not coming when it is.
    """
    order = _load(db, order_id)

    if actor_role == "customer" and order.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That order was not found.")

    allowed = {"customer": CUSTOMER_REASONS, "vendor": VENDOR_REASONS, "admin": ADMIN_REASONS}[actor_role]
    if reason_code not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That cancellation reason is not one we recognise.",
        )

    decision = st.can_request_cancellation(
        order,
        actor_role=actor_role,
        payment_attempt_open=_has_open_payment_attempt(db, order),
    )
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=decision.reason)

    now = datetime.now(UTC)
    order.cancellation_requested_at = now
    order.cancellation_requested_by = user.id
    order.cancellation_requested_role = actor_role
    order.cancellation_reason_code = reason_code
    order.cancellation_reason = note or reason_code.replace("_", " ").capitalize()

    if decision.requires_review:
        order.cancellation_status = st.CANCEL_REQUESTED
        record_order_status_event(
            db,
            order,
            status="cancellation_requested",
            actor_role=actor_role,
            note=decision.reason,
        )
        db.commit()
        db.refresh(order)
        for vendor_id in _order_vendor_ids(db, order):
            _notify(
                db,
                vendor_id,
                order=order,
                kind="order_cancellation_requested",
                title="Cancellation requested",
                body=f"The buyer asked to cancel order #{order.order_number}. Please respond.",
                accent="warning",
            )
        _notify(
            db,
            order.user_id,
            order=order,
            kind="order_cancellation_requested",
            title="Cancellation requested",
            body=decision.reason or "We've received your cancellation request.",
        )
        db.commit()
        return order

    return _finalise_cancellation(db, order, actor_role=actor_role, decided_by=user)


def decide_cancellation(
    db: Session,
    user: User,
    order_id: str,
    *,
    approve: bool,
    note: str | None = None,
    actor_role: str = "vendor",
) -> Order:
    """A vendor or admin answering a held request."""
    order = _load(db, order_id)

    decision = st.can_decide_cancellation(order)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=decision.reason)

    if actor_role == "vendor" and user.id not in _order_vendor_ids(db, order):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That order was not found.")

    order.cancellation_decided_at = datetime.now(UTC)
    order.cancellation_decided_by = user.id
    order.cancellation_decision_note = note

    if not approve:
        order.cancellation_status = st.CANCEL_REJECTED
        record_order_status_event(
            db, order, status="cancellation_rejected", actor_role=actor_role, note=note
        )
        db.commit()
        db.refresh(order)
        _notify(
            db,
            order.user_id,
            order=order,
            kind="order_cancellation_rejected",
            title="Cancellation declined",
            body=note or f"Your cancellation request for #{order.order_number} was declined.",
        )
        db.commit()
        return order

    return _finalise_cancellation(db, order, actor_role=actor_role, decided_by=user)


# ---------------------------------------------------------------------------
# Finalising
# ---------------------------------------------------------------------------


def _finalise_cancellation(
    db: Session, order: Order, *, actor_role: str, decided_by: User
) -> Order:
    """Stop the order and, if money was taken, open a refund for it.

    `payment_status` is deliberately left alone. Whether ODOS took money is a
    fact about the past and cancelling does not change it — overwriting it was
    exactly the bug that left fourteen orders unable to say whether they had
    ever been paid.
    """
    now = datetime.now(UTC)
    order.status = st.ORDER_CANCELLED
    order.cancellation_status = st.CANCEL_COMPLETED
    order.cancelled_at = now
    order.progress = 0
    order.tracking_eta = None
    order.cancellation_decided_at = order.cancellation_decided_at or now
    order.cancellation_decided_by = order.cancellation_decided_by or decided_by.id

    _restore_inventory(db, order)

    refund: OrderRefund | None = None
    if st.refund_is_owed(order):
        refund = _open_refund(db, order, initiated_by=decided_by)
        order.refund_status = st.REFUND_PENDING
    else:
        order.refund_status = st.REFUND_NOT_APPLICABLE

    record_order_status_event(
        db,
        order,
        status="cancelled",
        actor_role=actor_role,
        note=order.cancellation_reason,
    )
    db.commit()
    db.refresh(order)

    body = f"Order #{order.order_number} has been cancelled."
    if refund is not None:
        body += f" A refund of GHS {refund.amount:,.2f} is being processed."
    _notify(
        db,
        order.user_id,
        order=order,
        kind="order_cancelled",
        title="Order cancelled",
        body=body,
        accent="warning",
    )
    for vendor_id in _order_vendor_ids(db, order):
        _notify(
            db,
            vendor_id,
            order=order,
            kind="order_cancelled",
            title="Order cancelled",
            body=f"Order #{order.order_number} was cancelled. Do not prepare or dispatch it.",
            accent="warning",
        )
    db.commit()
    db.refresh(order)
    return order


def _restore_inventory(db: Session, order: Order) -> None:
    """Put stock back for an order that will never ship.

    Only for orders that have not left the vendor: once a parcel is out for
    delivery the goods are not on a shelf, and adding them back would oversell.
    """
    if st.vendor_stage(order) in st.AFTER_DISPATCH:
        return
    for item in order.items:
        if not item.product_id:
            continue
        product = db.get(Product, item.product_id)
        if product is None:
            continue
        # Through the ledger, not a bare assignment: the vendor's stock history
        # has to explain the jump, and this re-publishes anything the catalogue
        # auto-hid when it sold out.
        record_stock_change(
            db,
            product,
            new_stock=int(product.stock or 0) + int(item.quantity or 0),
            reason="return_restock",
            reference_type="order",
            reference_id=str(order.id),
            note=f"Order #{order.order_number} cancelled",
        )


def _open_refund(db: Session, order: Order, *, initiated_by: User, vendor_user_id=None) -> OrderRefund:
    existing = open_refund_for(db, order, vendor_user_id)
    if existing is not None:
        return existing

    quote = quote_refund(db, order, vendor_user_id=vendor_user_id)
    refund = OrderRefund(
        id=uuid.uuid4(),
        reference=refund_reference(),
        order_id=order.id,
        vendor_user_id=vendor_user_id,
        amount=quote.amount,
        items_amount=quote.items_amount,
        delivery_amount=quote.delivery_amount,
        discount_withheld=quote.discount_withheld,
        gateway_fee_withheld=quote.gateway_fee_withheld,
        status="pending",
        reason_code=order.cancellation_reason_code,
        reason_note=order.cancellation_reason,
        initiated_by=initiated_by.id,
    )
    db.add(refund)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return open_refund_for(db, order, vendor_user_id)
    return refund


# ---------------------------------------------------------------------------
# Refund lifecycle
# ---------------------------------------------------------------------------


def mark_refund_paid(
    db: Session,
    admin: User,
    refund_id: uuid.UUID,
    *,
    provider_reference: str,
    note: str | None = None,
) -> OrderRefund:
    """Record that the money actually went back.

    KutanaPay has no refund endpoint, so this is the authoritative step and a
    transfer reference is mandatory. Without one, "refunded" is a word someone
    typed rather than evidence of anything.
    """
    refund = db.get(OrderRefund, refund_id, with_for_update=True)
    if not refund:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found.")
    if refund.status == "successful":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That refund is already recorded as paid out.",
        )

    decision = st.can_mark_refund_successful(provider_reference)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=decision.reason)

    refund.status = "successful"
    refund.provider_reference = provider_reference.strip()
    refund.confirmed_by = admin.id
    refund.confirmed_at = datetime.now(UTC)
    refund.reason_note = note or refund.reason_note

    order = db.get(Order, refund.order_id)
    if order:
        paid_back = refunded_total(db, order) + float(refund.amount or 0)
        order.refund_status = (
            st.REFUND_SUCCESSFUL
            if paid_back >= float(order.total_amount or 0) - 0.01
            else st.REFUND_PARTIAL
        )
        record_order_status_event(
            db,
            order,
            status="refund_completed",
            actor_role="admin",
            note=f"{refund.reference} · {provider_reference}",
        )
    db.commit()
    db.refresh(refund)

    if order:
        _notify(
            db,
            order.user_id,
            order=order,
            kind="order_refund_successful",
            title="Refund sent",
            body=f"Your refund of GHS {refund.amount:,.2f} for #{order.order_number} has been paid out.",
            accent="success",
        )
        db.commit()
    return refund


def mark_refund_failed(
    db: Session, admin: User, refund_id: uuid.UUID, *, reason: str
) -> OrderRefund:
    """A transfer that did not go through. The order stays cancelled."""
    refund = db.get(OrderRefund, refund_id, with_for_update=True)
    if not refund:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refund not found.")
    if refund.status == "successful":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That refund has already been paid out.",
        )

    refund.status = "failed"
    refund.failure_reason = reason
    refund.confirmed_by = admin.id
    refund.confirmed_at = datetime.now(UTC)

    order = db.get(Order, refund.order_id)
    if order:
        order.refund_status = st.REFUND_FAILED
        record_order_status_event(
            db, order, status="refund_failed", actor_role="admin", note=reason
        )
    db.commit()
    db.refresh(refund)

    if order:
        _notify(
            db,
            order.user_id,
            order=order,
            kind="order_refund_failed",
            title="Refund needs attention",
            body=f"Your refund for #{order.order_number} did not go through. We're on it.",
            accent="warning",
        )
        db.commit()
    return refund


# ---------------------------------------------------------------------------
# Admin and vendor queues
# ---------------------------------------------------------------------------


def list_refunds(
    db: Session,
    *,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OrderRefund]:
    """The refund desk. Ordered oldest-first on purpose: a refund waiting three
    days matters more than one raised this morning."""
    query = select(OrderRefund).order_by(OrderRefund.created_at.asc())
    cleaned = (status_filter or "").strip().lower()
    if cleaned and cleaned != "all":
        query = query.where(OrderRefund.status == cleaned)
    return list(db.scalars(query.offset(offset).limit(limit)).all())


def list_cancellation_requests(
    db: Session,
    *,
    vendor_user_id=None,
    limit: int = 50,
    offset: int = 0,
) -> list[Order]:
    """Cancellations awaiting a decision, oldest first.

    Scoped to one vendor's own orders when a vendor asks, so the queue can be
    served to either audience from one place without leaking across shops.
    """
    query = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.cancellation_status == st.CANCEL_REQUESTED)
        .order_by(Order.cancellation_requested_at.asc())
    )
    rows = list(db.scalars(query.offset(offset).limit(limit * 4 if vendor_user_id else limit)).all())
    if vendor_user_id is None:
        return rows[:limit]
    mine = [order for order in rows if vendor_user_id in _order_vendor_ids(db, order)]
    return mine[:limit]


def settle_cancelled_package(
    db: Session,
    order: Order,
    package,
    *,
    initiated_by: User,
    previous_stage: str,
) -> OrderRefund | None:
    """The money and stock consequences of one package being cancelled.

    Shared with `update_vendor_order_status`, which marks a package cancelled
    straight from the fulfilment ladder. That path used to end there — the bag
    was marked cancelled and nothing else happened, so a vendor could cancel a
    paid package and the shopper got no refund and the stock never came back,
    while the web UI told them a refund was on its way. Both paths now land
    here so a cancelled package always costs the same thing.

    `previous_stage` is the package's stage *before* it was marked cancelled.
    Both callers overwrite `vendor_status` first, so reading it here would
    always see "cancelled" and the dispatch check below would never fire.

    Does not commit: the caller owns the transaction.
    """
    vendor_user_id = package.vendor_user_id if package is not None else None

    # Only before dispatch. Once a parcel is in a van the goods are not on a
    # shelf, and adding them back would oversell.
    if (previous_stage or "").strip().lower() not in st.AFTER_DISPATCH:
        for item in order.items:
            if vendor_user_id is not None and item.vendor_user_id != vendor_user_id:
                continue
            if not item.product_id:
                continue
            product = db.get(Product, item.product_id)
            if product is not None:
                record_stock_change(
                    db,
                    product,
                    new_stock=int(product.stock or 0) + int(item.quantity or 0),
                    reason="return_restock",
                    reference_type="order",
                    reference_id=str(order.id),
                    note=f"Package cancelled on order #{order.order_number}",
                )

    refund: OrderRefund | None = None
    if st.refund_is_owed(order):
        refund = _open_refund(db, order, initiated_by=initiated_by, vendor_user_id=vendor_user_id)
        order.refund_status = st.REFUND_PENDING

    # The parent order only closes when nothing is left to fulfil.
    live_packages = [
        p for p in (order.packages or []) if (p.vendor_status or "") != "cancelled"
    ]
    if not live_packages:
        order.status = st.ORDER_CANCELLED
        order.cancellation_status = st.CANCEL_COMPLETED
        order.cancelled_at = datetime.now(UTC)

    return refund


def cancel_vendor_package(
    db: Session,
    actor: User,
    order_id: str,
    vendor_user_id,
    *,
    reason_code: str,
    note: str | None = None,
    actor_role: str = "vendor",
) -> OrderRefund | None:
    """Cancel one vendor's part of a multi-vendor order.

    The other vendors' packages are untouched — one shop running out of stock
    is not a reason to unwind a basket that three shops could otherwise fill.
    The refund covers only the cancelled lines plus that package's own delivery,
    with the cart discount apportioned by line value so the surviving items keep
    the share they earned.
    """
    order = _load(db, order_id)

    # Validated for the same reason the shopper path validates: the code ends
    # up in the timeline and in what the shopper is told, so it cannot be an
    # arbitrary string a caller invented.
    allowed = {"vendor": VENDOR_REASONS, "admin": ADMIN_REASONS}.get(actor_role, VENDOR_REASONS)
    if reason_code not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That cancellation reason is not one we recognise.",
        )

    if actor_role == "vendor" and actor.id != vendor_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That order was not found.")
    if vendor_user_id not in _order_vendor_ids(db, order):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That vendor has nothing on this order.",
        )
    if st.order_is_closed(order):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This order is {order.status} and cannot be changed.",
        )

    package = next(
        (p for p in (order.packages or []) if p.vendor_user_id == vendor_user_id), None
    )
    previous_stage = (package.vendor_status or "") if package is not None else st.vendor_stage(order)
    if package is not None:
        if previous_stage in st.AFTER_DISPATCH:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That package has already been dispatched and cannot be cancelled here.",
            )
        package.vendor_status = "cancelled"

    refund = settle_cancelled_package(
        db, order, package, initiated_by=actor, previous_stage=previous_stage
    )

    record_order_status_event(
        db,
        order,
        status="vendor_package_cancelled",
        actor_role=actor_role,
        note=note or reason_code,
    )
    db.commit()

    _notify(
        db,
        order.user_id,
        order=order,
        kind="order_item_cancelled",
        title="Part of your order was cancelled",
        body=(
            f"One seller on order #{order.order_number} cannot fulfil their items."
            + (f" A refund of GHS {refund.amount:,.2f} is being processed." if refund else "")
        ),
        accent="warning",
    )
    db.commit()
    return refund
