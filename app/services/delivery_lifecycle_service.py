"""Single authoritative implementation of the delivery lifecycle.

Business model: Vendor -> vendor's own (external) delivery rider -> Customer.
There is no rider app and no rider GPS/acceptance/route tracking — the vendor
dispatches, and the customer is the one who confirms the handoff actually
happened. A vendor can never confirm delivery themselves: they would have
every incentive to claim a handoff that never happened, so letting them
self-certify defeats the point of "proof of delivery" entirely.

Three independent concerns are tracked, on purpose:
  - Order state    (Order.status / Order.vendor_status) — vendor fulfillment
    stages (pending/confirmed/processing/ready) plus the coarse outcome
    (processing/delivered/cancelled) already used throughout the app.
  - Delivery state  (Order.delivery_status) — the finer-grained journey
    between "vendor dispatched" and "customer has it", including branches
    (rescheduled / customer_problem) vendor_status has no room to express.
  - Settlement state (Order.settlement_status, VendorWalletTransaction) — was
    the vendor actually paid, kept separate so a delivery problem can hold
    payment without touching delivery status semantics.

vendor_controller / order_controller / admin_controller / the auto-release
job all call into the functions below rather than mutating these fields
directly — this module is the only place a delivery transition happens.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event, order_notification_image
from app.controllers.wallet_controller import publish_vendor_wallet_updates, settle_vendor_wallets_for_order
from app.models import Order, User
from app.services.order_timeline_service import record_order_status_event
from app.services.push_service import dispatch_customer_order_push
from app.services.sms_service import send_delivery_out_for_delivery_sms

logger = logging.getLogger(__name__)

# --- Delivery status ---
NOT_DISPATCHED = "not_dispatched"
OUT_FOR_DELIVERY = "out_for_delivery"
RESCHEDULED = "rescheduled"
CUSTOMER_PROBLEM = "customer_problem"
DELIVERED = "delivered"
FAILED = "failed"

# --- Confirmation method (who/what completed delivery) ---
CONFIRMATION_CUSTOMER = "customer"
CONFIRMATION_AUTO_RELEASE = "auto_release"
CONFIRMATION_ADMIN_OVERRIDE = "admin_override"

# --- Settlement status ---
SETTLEMENT_NOT_ELIGIBLE = "not_eligible"
SETTLEMENT_ELIGIBLE = "eligible"
SETTLEMENT_SETTLED = "settled"
SETTLEMENT_HELD = "held"

AUTO_RELEASE_GRACE_HOURS = 48
AUTO_RELEASE_REMINDER_HOURS = 36
RESCHEDULE_THROTTLE_MINUTES = 10

DELIVERY_PROBLEM_REASONS = {
    "rider_no_show",
    "not_available",
    "wrong_delivery",
    "order_issue",
    "other",
}


class DeliveryError(HTTPException):
    """A delivery business-rule violation. Carries a stable `code` (see
    docstring below) alongside the human-readable `detail` FastAPI already
    serializes, so clients can branch on it instead of string-matching."""

    def __init__(self, code: str, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def _err(code: str, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> DeliveryError:
    return DeliveryError(code=code, detail=detail, status_code=status_code)


def _lock_order_for_customer(db: Session, user_id: uuid.UUID, order_id: str) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
        .where(Order.id == order_id, Order.user_id == user_id)
        .with_for_update()
    )
    if not order:
        raise _err("ORDER_NOT_FOUND", "That order was not found.", status.HTTP_404_NOT_FOUND)
    return order


def _lock_order_by_id(db: Session, order_id) -> Order | None:
    return db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
        .where(Order.id == order_id)
        .with_for_update()
    )


def _record_event(
    db: Session,
    order: Order,
    *,
    status_value: str,
    actor_role: str,
    actor_id: uuid.UUID | None = None,
    note: str | None = None,
    event_type: str,
    extra_metadata: dict | None = None,
) -> None:
    metadata = {"event_type": event_type}
    if extra_metadata:
        metadata.update(extra_metadata)
    record_order_status_event(
        db,
        order,
        status=status_value,
        actor_role=actor_role,
        note=note,
        actor_id=actor_id,
        event_metadata=metadata,
    )


# --------------------------------------------------------------------------
# Dispatch — vendor -> rider handoff. The vendor's last self-serve action.
# --------------------------------------------------------------------------


def dispatch_order(db: Session, order: Order, *, actor: User) -> bool:
    """Marks an order out for delivery. Idempotent: calling this while
    already out_for_delivery is a no-op (returns False, no duplicate SMS/
    event). Returns True if this call actually advanced the order (either a
    first dispatch or a redispatch after reschedule).

    Caller (vendor_controller) is responsible for ownership/authorization
    and for the order/vendor_status bookkeeping outside the delivery
    sub-state — this only owns the delivery_status transition.
    """
    if order.payment_status != "paid":
        raise _err(
            "ORDER_NOT_READY_FOR_DISPATCH",
            "This order hasn't been paid for yet — it can't be dispatched.",
        )
    if order.delivery_status == DELIVERED:
        raise _err("DELIVERY_ALREADY_COMPLETED", "This order has already been delivered.")
    if order.delivery_status == OUT_FOR_DELIVERY:
        return False
    if order.delivery_status not in (NOT_DISPATCHED, RESCHEDULED):
        raise _err(
            "ORDER_NOT_READY_FOR_DISPATCH",
            f"This order can't be dispatched from its current delivery state ({order.delivery_status}).",
        )

    is_redispatch = order.delivery_status == RESCHEDULED
    now = datetime.now(UTC)
    order.delivery_status = OUT_FOR_DELIVERY
    order.dispatched_at = now
    order.dispatch_attempt_count += 1
    order.auto_release_at = now + timedelta(hours=AUTO_RELEASE_GRACE_HOURS)
    order.delivery_reminder_sent_at = None
    order.reschedule_requested_at = None
    order.reschedule_note = None

    _record_event(
        db,
        order,
        status_value=OUT_FOR_DELIVERY,
        actor_role="vendor",
        actor_id=actor.id,
        event_type="REDISPATCHED" if is_redispatch else "DISPATCHED",
        extra_metadata={"attempt": order.dispatch_attempt_count},
    )

    if order.address_phone:
        try:
            send_delivery_out_for_delivery_sms(
                phone_number=order.address_phone,
                order_number=order.order_number,
            )
        except Exception:
            logger.exception("Failed to send out-for-delivery SMS for order %s", order.id)

    logger.info(
        "delivery.dispatched order_id=%s attempt=%s redispatch=%s",
        order.id,
        order.dispatch_attempt_count,
        is_redispatch,
    )
    return True


# --------------------------------------------------------------------------
# Completion — the one place "delivered" is ever set, regardless of who/what
# triggered it. Settlement is triggered from here and nowhere else.
# --------------------------------------------------------------------------


def _complete_delivery(
    db: Session,
    order: Order,
    *,
    confirmation_method: str,
    actor_role: str,
    actor_id: uuid.UUID | None,
    note: str | None = None,
    event_type: str,
) -> set[uuid.UUID]:
    previous_delivery_status = order.delivery_status
    now = datetime.now(UTC)

    order.delivery_status = DELIVERED
    order.status = "delivered"
    order.vendor_status = "delivered"
    order.progress = 1
    order.tracking_eta = None
    order.cancelled_at = None
    order.cancellation_reason = None
    order.delivered_at = now
    order.confirmation_method = confirmation_method
    order.settlement_status = SETTLEMENT_ELIGIBLE

    _record_event(
        db,
        order,
        status_value="delivered",
        actor_role=actor_role,
        actor_id=actor_id,
        note=note,
        event_type=event_type,
        extra_metadata={
            "previous_delivery_status": previous_delivery_status,
            "confirmation_method": confirmation_method,
        },
    )

    # Settlement is transactional with delivery completion (same DB
    # transaction, same commit) rather than a separate async pipeline —
    # crediting a wallet is pure Postgres arithmetic here, not an external
    # payment-gateway call, so there's nothing to make "pending" about it.
    changed_wallet_vendor_ids = settle_vendor_wallets_for_order(db, order)
    order.settlement_status = SETTLEMENT_SETTLED
    return changed_wallet_vendor_ids


def _notify_customer_delivered(db: Session, order: Order, *, title: str, body: str) -> None:
    preview = order_notification_image(order)
    event = create_notification_event(
        db,
        order.user,
        kind="order_delivered",
        title=title,
        body=body,
        icon="checkmark-done-outline",
        accent="success",
        action_label="View receipt",
        route_type="order",
        route_target_id=str(order.id),
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )
    dispatch_customer_order_push(user=order.user, title=title, body=body, order=order, notification_event=event)


def confirm_delivery_by_customer(db: Session, user: User, order_id: str) -> tuple[Order, set[uuid.UUID]]:
    """The customer's own confirmation. Idempotent: confirming an
    already-delivered order is a harmless no-op, not an error, so a retried
    mobile request never surfaces a scary failure for a request that already
    succeeded."""
    order = _lock_order_for_customer(db, user.id, order_id)

    if order.delivery_status == DELIVERED:
        return order, set()
    if order.delivery_status not in (OUT_FOR_DELIVERY, CUSTOMER_PROBLEM):
        raise _err(
            "DELIVERY_NOT_STARTED",
            "This order hasn't been dispatched yet — there's nothing to confirm.",
        )

    changed_wallet_vendor_ids = _complete_delivery(
        db,
        order,
        confirmation_method=CONFIRMATION_CUSTOMER,
        actor_role="customer",
        actor_id=user.id,
        note="Customer confirmed receipt",
        event_type="CUSTOMER_CONFIRMED",
    )
    _notify_customer_delivered(
        db,
        order,
        title="Order delivered",
        body=f"Order #{order.order_number} has arrived successfully.",
    )
    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    logger.info("delivery.customer_confirmed order_id=%s user_id=%s", order.id, user.id)
    return order, changed_wallet_vendor_ids


def report_delivery_problem(db: Session, user: User, order_id: str, *, reason: str, details: str | None) -> Order:
    order = _lock_order_for_customer(db, user.id, order_id)

    if order.delivery_status == DELIVERED:
        raise _err("DELIVERY_ALREADY_COMPLETED", "This order has already been marked delivered.")
    if order.delivery_status != OUT_FOR_DELIVERY:
        raise _err(
            "DELIVERY_NOT_STARTED",
            "This order isn't currently out for delivery.",
        )

    reason_key = reason if reason in DELIVERY_PROBLEM_REASONS else "other"
    now = datetime.now(UTC)
    order.delivery_status = CUSTOMER_PROBLEM
    order.settlement_status = SETTLEMENT_HELD
    order.delivery_problem_reason = details.strip() if details else reason_key
    order.delivery_problem_reported_at = now

    _record_event(
        db,
        order,
        status_value=CUSTOMER_PROBLEM,
        actor_role="customer",
        actor_id=user.id,
        note=details or reason_key,
        event_type="CUSTOMER_REPORTED_PROBLEM",
        extra_metadata={"reason": reason_key},
    )
    db.commit()
    db.refresh(order)
    logger.info("delivery.problem_reported order_id=%s reason=%s", order.id, reason_key)
    return order


def mark_rescheduled(db: Session, order: Order, *, note: str | None) -> None:
    """State mutation only — order_controller.request_order_reschedule keeps
    ownership of the throttle check and vendor notification dispatch (that's
    presentation/notification concern, not a delivery-state concern)."""
    order.delivery_status = RESCHEDULED
    order.reschedule_requested_at = datetime.now(UTC)
    order.reschedule_note = note
    _record_event(
        db,
        order,
        status_value=RESCHEDULED,
        actor_role="customer",
        note=note,
        event_type="RESCHEDULED",
    )


def send_auto_release_reminder(db: Session, order: Order) -> None:
    """Idempotency guard is `delivery_reminder_sent_at IS NULL` in the
    caller's candidate query — this just performs the send + marks it sent
    in the same transaction so a crash between the two can't double-send."""
    title = "Confirm your delivery"
    body = (
        f"Order #{order.order_number} will be automatically marked delivered soon if you "
        "don't confirm it or let us know about a problem."
    )
    preview = order_notification_image(order)
    event = create_notification_event(
        db,
        order.user,
        kind="order_delivery_reminder",
        title=title,
        body=body,
        icon="time-outline",
        accent="warning",
        action_label="Review order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )
    dispatch_customer_order_push(user=order.user, title=title, body=body, order=order, notification_event=event)
    order.delivery_reminder_sent_at = datetime.now(UTC)
    _record_event(
        db,
        order,
        status_value=order.delivery_status,
        actor_role="system",
        note="Auto-release reminder sent",
        event_type="AUTO_RELEASE_REMINDER",
    )
    db.commit()


def is_eligible_for_auto_release(order, now: datetime) -> bool:
    """Pure predicate (works against any object with these attributes, real
    Order or test double) so Invariant 9 — auto-release never bypasses an
    active exception — is independently unit-testable without a DB."""
    return (
        order.delivery_status == OUT_FOR_DELIVERY
        and order.status not in {"cancelled", "delivered", "refunded"}
        and order.payment_status == "paid"
        and order.auto_release_at is not None
        and order.auto_release_at <= now
    )


def auto_release_delivery(db: Session, order_id) -> tuple[Order | None, set[uuid.UUID]]:
    """Re-validates eligibility *inside* the row lock immediately before
    acting, so a customer confirmation or problem report that lands in the
    same instant as this job never gets silently overwritten (Invariant 9)."""
    order = _lock_order_by_id(db, order_id)
    if not order:
        return None, set()

    now = datetime.now(UTC)
    if not is_eligible_for_auto_release(order, now):
        return order, set()

    changed_wallet_vendor_ids = _complete_delivery(
        db,
        order,
        confirmation_method=CONFIRMATION_AUTO_RELEASE,
        actor_role="system",
        actor_id=None,
        note=f"Auto-confirmed {AUTO_RELEASE_GRACE_HOURS}h after dispatch — no customer response",
        event_type="AUTO_RELEASED",
    )
    order.auto_released_at = now
    _notify_customer_delivered(
        db,
        order,
        title="Order marked delivered",
        body=(
            f"We didn't hear back on order #{order.order_number}, so we've marked it delivered. "
            "Contact support if that's not right."
        ),
    )
    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    logger.info("delivery.auto_released order_id=%s", order.id)
    return order, changed_wallet_vendor_ids


def admin_override_deliver(db: Session, admin_user: User, order_id: str, *, reason: str) -> tuple[Order, set[uuid.UUID]]:
    if not reason or not reason.strip():
        raise _err("ADMIN_REASON_REQUIRED", "An override reason is required to force-complete a delivery.")

    order = _lock_order_by_id(db, order_id)
    if not order:
        raise _err("ORDER_NOT_FOUND", "That order was not found.", status.HTTP_404_NOT_FOUND)
    if order.payment_status != "paid":
        raise _err("ORDER_NOT_READY_FOR_DISPATCH", "Only paid orders can be marked delivered.")
    if order.delivery_status == DELIVERED:
        return order, set()

    changed_wallet_vendor_ids = _complete_delivery(
        db,
        order,
        confirmation_method=CONFIRMATION_ADMIN_OVERRIDE,
        actor_role="admin",
        actor_id=admin_user.id,
        note=reason.strip(),
        event_type="ADMIN_OVERRIDE",
    )
    _notify_customer_delivered(
        db,
        order,
        title="Order delivered",
        body=f"Order #{order.order_number} has been marked delivered by ODOS support.",
    )
    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    logger.info("delivery.admin_override order_id=%s admin_id=%s", order.id, admin_user.id)
    return order, changed_wallet_vendor_ids
