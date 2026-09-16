"""Single authoritative implementation of the delivery lifecycle.

Business model: Vendor -> vendor's own (external) delivery rider -> Customer.
There is no rider app and no rider GPS/acceptance/route tracking — the vendor
dispatches, and the customer is the one who confirms the handoff actually
happened. A vendor can never confirm delivery themselves: they would have
every incentive to claim a handoff that never happened, so letting them
self-certify defeats the point of "proof of delivery" entirely.

Since the package split, every one of the concerns below is tracked **per
package** -- one vendor's bag -- and the matching columns on Order are a
derived roll-up maintained by order_package_service.recompute_order_rollup.
The order-level functions here (confirm, report a problem, dispatch) still
exist and still take an order: they now simply apply to every package the
action makes sense for, which for the single-vendor orders that make up most
of the platform is exactly the one package and exactly the old behaviour.

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
from app.models import Order, OrderPackage, User
from app.services.order_package_service import ensure_packages, recompute_order_rollup
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
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.timeline),
            selectinload(Order.packages),
        )
        .where(Order.id == order_id, Order.user_id == user_id)
        .with_for_update()
    )
    if not order:
        raise _err("ORDER_NOT_FOUND", "That order was not found.", status.HTTP_404_NOT_FOUND)
    return order


def _lock_order_by_id(db: Session, order_id) -> Order | None:
    return db.scalar(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.timeline),
            selectinload(Order.packages),
        )
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


def dispatch_package(
    db: Session, order: Order, package: OrderPackage, *, actor: User
) -> bool:
    """Send one vendor's package out with their rider.

    Idempotent: dispatching an already-dispatched package is a no-op (returns
    False, no duplicate SMS or event), so a retried tap never double-notifies.

    This is the function that used to be `dispatch_order`, and moving it down
    to the package is the fix for the worst of the shared-state bugs: the
    first vendor on a three-shop order to hand their bag to a rider used to
    flip the *whole* order to out_for_delivery, tell the customer everything
    was on its way, and start a 48-hour auto-release clock against items still
    sitting on two other shelves. Now it starts a clock for one bag.
    """
    if order.payment_status != "paid":
        raise _err(
            "ORDER_NOT_READY_FOR_DISPATCH",
            "This order hasn't been paid for yet — it can't be dispatched.",
        )
    if package.delivery_status == DELIVERED:
        raise _err("DELIVERY_ALREADY_COMPLETED", "This package has already been delivered.")
    if package.delivery_status == OUT_FOR_DELIVERY:
        return False
    if package.delivery_status not in (NOT_DISPATCHED, RESCHEDULED):
        raise _err(
            "ORDER_NOT_READY_FOR_DISPATCH",
            "This package can't be dispatched from its current delivery state "
            f"({package.delivery_status}).",
        )

    is_redispatch = package.delivery_status == RESCHEDULED
    now = datetime.now(UTC)
    package.delivery_status = OUT_FOR_DELIVERY
    package.vendor_status = "out_for_delivery"
    package.dispatched_at = now
    package.dispatch_attempt_count += 1
    # Each package gets its own grace window, counted from its own dispatch.
    # A customer who receives the dress on Monday and the sneakers on Thursday
    # has 48 hours to speak up about each, not 48 hours from whichever shop
    # happened to move first.
    package.auto_release_at = now + timedelta(hours=AUTO_RELEASE_GRACE_HOURS)
    package.delivery_reminder_sent_at = None
    package.reschedule_requested_at = None
    package.reschedule_note = None
    package.tracking_eta = "Out for delivery · on the way to you"

    recompute_order_rollup(order)

    _record_event(
        db,
        order,
        status_value=OUT_FOR_DELIVERY,
        actor_role="vendor",
        actor_id=actor.id,
        note=_package_note(order, package, "dispatched"),
        event_type="REDISPATCHED" if is_redispatch else "DISPATCHED",
        extra_metadata={
            "attempt": package.dispatch_attempt_count,
            "package_id": str(package.id),
            "package_number": package.package_number,
            "store_name": package.store_name,
        },
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
        "delivery.dispatched order_id=%s package_id=%s attempt=%s redispatch=%s",
        order.id,
        package.id,
        package.dispatch_attempt_count,
        is_redispatch,
    )
    return True


def _package_note(order: Order, package: OrderPackage, verb: str) -> str:
    """Timeline copy that names the shop when there is more than one.

    On a single-vendor order naming the shop is noise, and the timeline should
    read exactly as it did before packages existed.
    """
    if len(order.packages) <= 1:
        return f"Order {verb}"
    label = package.store_name or f"Package {package.package_number}"
    return f"{label} {verb}"


def dispatch_order(db: Session, order: Order, *, actor: User) -> bool:
    """Order-level dispatch: send out every package that is ready to go.

    Kept for the admin and legacy paths that genuinely mean "the whole order".
    The vendor path no longer comes through here -- a vendor can only dispatch
    their own package, which is the entire point.
    """
    packages = ensure_packages(db, order)
    if not packages:
        raise _err(
            "ORDER_NOT_READY_FOR_DISPATCH",
            "This order has no vendor packages to dispatch.",
        )
    dispatched = False
    for package in packages:
        if package.vendor_status == "cancelled":
            continue
        if package.delivery_status in (NOT_DISPATCHED, RESCHEDULED):
            dispatched = dispatch_package(db, order, package, actor=actor) or dispatched
    return dispatched


# --------------------------------------------------------------------------
# Completion — the one place "delivered" is ever set, regardless of who/what
# triggered it. Settlement is triggered from here and nowhere else.
# --------------------------------------------------------------------------


def _complete_package(
    db: Session,
    order: Order,
    package: OrderPackage,
    *,
    confirmation_method: str,
    actor_role: str,
    actor_id: uuid.UUID | None,
    note: str | None = None,
    event_type: str,
) -> set[uuid.UUID]:
    """Mark one package delivered and pay the vendor who carried it.

    The only place a package becomes DELIVERED, regardless of who triggered
    it -- customer, auto-release, or admin override -- and the only place
    settlement fires.

    Settlement is scoped to this package's vendor. Before packages it was not
    scoped at all, so a customer confirming the one bag that arrived credited
    every vendor on the order, including the ones still holding their items.
    """
    previous_delivery_status = package.delivery_status
    now = datetime.now(UTC)

    package.delivery_status = DELIVERED
    package.vendor_status = "delivered"
    package.delivered_at = now
    package.confirmation_method = confirmation_method
    package.settlement_status = SETTLEMENT_ELIGIBLE
    package.tracking_eta = None
    package.auto_release_at = None

    _record_event(
        db,
        order,
        status_value="delivered",
        actor_role=actor_role,
        actor_id=actor_id,
        note=note or _package_note(order, package, "delivered"),
        event_type=event_type,
        extra_metadata={
            "previous_delivery_status": previous_delivery_status,
            "confirmation_method": confirmation_method,
            "package_id": str(package.id),
            "package_number": package.package_number,
            "store_name": package.store_name,
        },
    )

    # Settlement is transactional with delivery completion (same DB
    # transaction, same commit) rather than a separate async pipeline —
    # crediting a wallet is pure Postgres arithmetic here, not an external
    # payment-gateway call, so there's nothing to make "pending" about it.
    changed_wallet_vendor_ids: set[uuid.UUID] = set()
    if package.vendor_user_id:
        changed_wallet_vendor_ids = settle_vendor_wallets_for_order(
            db, order, vendor_scope={package.vendor_user_id}
        )
    package.settlement_status = SETTLEMENT_SETTLED

    recompute_order_rollup(order)
    if order.delivery_status == DELIVERED:
        order.status = "delivered"
        order.tracking_eta = None
        order.cancelled_at = None
        order.cancellation_reason = None
        order.confirmation_method = confirmation_method
    return changed_wallet_vendor_ids


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
    """Complete every package on the order that is still outstanding.

    The order-level entry point, for callers that mean the whole order (admin
    override, and a customer tapping "I got everything"). On the single-vendor
    orders that are most of the platform this completes exactly one package,
    which is exactly what it did before.
    """
    packages = ensure_packages(db, order)
    changed: set[uuid.UUID] = set()
    for package in packages:
        if package.vendor_status == "cancelled" or package.delivery_status == DELIVERED:
            continue
        changed |= _complete_package(
            db,
            order,
            package,
            confirmation_method=confirmation_method,
            actor_role=actor_role,
            actor_id=actor_id,
            note=note,
            event_type=event_type,
        )
    return changed


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


def _resolve_package(order: Order, package_id) -> OrderPackage:
    package = next((p for p in order.packages if str(p.id) == str(package_id)), None)
    if package is None:
        raise _err(
            "PACKAGE_NOT_FOUND",
            "That package isn't part of this order.",
            status.HTTP_404_NOT_FOUND,
        )
    return package


def confirm_delivery_by_customer(
    db: Session, user: User, order_id: str, *, package_id=None
) -> tuple[Order, set[uuid.UUID]]:
    """The customer's own confirmation.

    With `package_id`, confirms just that shop's bag -- which is what a
    customer does when the dress arrives on Monday and the sneakers are still
    coming. Without it, confirms everything still outstanding, which is both
    the old behaviour and the right behaviour for a single-vendor order.

    Idempotent: confirming something already delivered is a harmless no-op,
    not an error, so a retried mobile request never surfaces a scary failure
    for a request that already succeeded.
    """
    order = _lock_order_for_customer(db, user.id, order_id)
    packages = ensure_packages(db, order)

    if package_id is not None:
        targets = [_resolve_package(order, package_id)]
    else:
        targets = [p for p in packages if p.vendor_status != "cancelled"]

    outstanding = [p for p in targets if p.delivery_status != DELIVERED]
    if not outstanding:
        return order, set()

    not_dispatched = [
        p for p in outstanding if p.delivery_status not in (OUT_FOR_DELIVERY, CUSTOMER_PROBLEM)
    ]
    if len(not_dispatched) == len(outstanding):
        raise _err(
            "DELIVERY_NOT_STARTED",
            "This hasn't been dispatched yet — there's nothing to confirm.",
        )

    changed_wallet_vendor_ids: set[uuid.UUID] = set()
    for package in outstanding:
        # A package still being packed is skipped rather than rejected: on a
        # multi-shop order the customer is confirming what arrived, and
        # failing the whole request because one other bag hasn't shipped would
        # punish them for the slow shop.
        if package.delivery_status not in (OUT_FOR_DELIVERY, CUSTOMER_PROBLEM):
            continue
        changed_wallet_vendor_ids |= _complete_package(
            db,
            order,
            package,
            confirmation_method=CONFIRMATION_CUSTOMER,
            actor_role="customer",
            actor_id=user.id,
            note=_package_note(order, package, "confirmed received by customer"),
            event_type="CUSTOMER_CONFIRMED",
        )

    remaining = [
        p
        for p in order.packages
        if p.vendor_status != "cancelled" and p.delivery_status != DELIVERED
    ]
    if remaining:
        _notify_customer_delivered(
            db,
            order,
            title="Package confirmed",
            body=(
                f"Thanks — that's confirmed for order #{order.order_number}. "
                f"{len(remaining)} more on the way."
            ),
        )
    else:
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
    logger.info(
        "delivery.customer_confirmed order_id=%s user_id=%s packages=%s",
        order.id,
        user.id,
        len(outstanding),
    )
    return order, changed_wallet_vendor_ids


def report_delivery_problem(
    db: Session, user: User, order_id: str, *, reason: str, details: str | None, package_id=None
) -> Order:
    """Flag a problem with one package, or with everything still in flight.

    Holding settlement is scoped to the package in trouble. A customer
    reporting that the sneakers never showed up must not freeze the money owed
    to the shop whose dress arrived exactly as promised.
    """
    order = _lock_order_for_customer(db, user.id, order_id)
    packages = ensure_packages(db, order)

    if package_id is not None:
        targets = [_resolve_package(order, package_id)]
    else:
        targets = [p for p in packages if p.vendor_status != "cancelled"]

    if targets and all(p.delivery_status == DELIVERED for p in targets):
        raise _err("DELIVERY_ALREADY_COMPLETED", "This has already been marked delivered.")

    actionable = [p for p in targets if p.delivery_status == OUT_FOR_DELIVERY]
    if not actionable:
        raise _err("DELIVERY_NOT_STARTED", "This isn't currently out for delivery.")

    reason_key = reason if reason in DELIVERY_PROBLEM_REASONS else "other"
    now = datetime.now(UTC)

    for package in actionable:
        package.delivery_status = CUSTOMER_PROBLEM
        package.settlement_status = SETTLEMENT_HELD
        package.delivery_problem_reason = details.strip() if details else reason_key
        package.delivery_problem_reported_at = now
        # The clock stops while a problem is open. Auto-releasing a delivery
        # the customer has just told us went wrong would be the single worst
        # thing this system could do with a grace window.
        package.auto_release_at = None

        _record_event(
            db,
            order,
            status_value=CUSTOMER_PROBLEM,
            actor_role="customer",
            actor_id=user.id,
            note=details or reason_key,
            event_type="CUSTOMER_REPORTED_PROBLEM",
            extra_metadata={
                "reason": reason_key,
                "package_id": str(package.id),
                "package_number": package.package_number,
                "store_name": package.store_name,
            },
        )

    recompute_order_rollup(order)
    db.commit()
    db.refresh(order)
    logger.info(
        "delivery.problem_reported order_id=%s reason=%s packages=%s",
        order.id,
        reason_key,
        len(actionable),
    )
    return order


def mark_rescheduled(db: Session, order: Order, *, note: str | None, package_id=None) -> None:
    """State mutation only — order_controller.request_order_reschedule keeps
    ownership of the throttle check and vendor notification dispatch (that's
    presentation/notification concern, not a delivery-state concern)."""
    packages = ensure_packages(db, order)
    if package_id is not None:
        targets = [_resolve_package(order, package_id)]
    else:
        targets = [p for p in packages if p.delivery_status == OUT_FOR_DELIVERY]

    now = datetime.now(UTC)
    for package in targets:
        package.delivery_status = RESCHEDULED
        package.reschedule_requested_at = now
        package.reschedule_note = note
        # A rescheduled package is no longer in transit, so its grace window
        # must not keep ticking towards auto-release. The clock restarts when
        # the vendor dispatches again.
        package.auto_release_at = None
        _record_event(
            db,
            order,
            status_value=RESCHEDULED,
            actor_role="customer",
            note=note,
            event_type="RESCHEDULED",
            extra_metadata={
                "package_id": str(package.id),
                "package_number": package.package_number,
                "store_name": package.store_name,
            },
        )
    recompute_order_rollup(order)


def send_auto_release_reminder(db: Session, order: Order, package: OrderPackage) -> None:
    """Idempotency guard is `delivery_reminder_sent_at IS NULL` in the
    caller's candidate query — this just performs the send + marks it sent
    in the same transaction so a crash between the two can't double-send."""
    label = (
        package.store_name or f"Package {package.package_number}"
        if len(order.packages) > 1
        else f"Order #{order.order_number}"
    )
    title = "Confirm your delivery"
    body = (
        f"{label} will be automatically marked delivered soon if you don't "
        "confirm it or let us know about a problem."
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
    package.delivery_reminder_sent_at = datetime.now(UTC)
    _record_event(
        db,
        order,
        status_value=package.delivery_status,
        actor_role="system",
        note=f"Auto-release reminder sent · {label}",
        event_type="AUTO_RELEASE_REMINDER",
        extra_metadata={"package_id": str(package.id)},
    )
    db.commit()


def is_eligible_for_auto_release(order, package, now: datetime) -> bool:
    """Pure predicate (works against any object with these attributes, real
    rows or test doubles) so Invariant 9 — auto-release never bypasses an
    active exception — is independently unit-testable without a DB.

    Both halves are checked: the *order* must still be live and paid, and the
    *package* must still be out for delivery with an elapsed clock. Splitting
    it this way is what stops a cancelled or refunded order from having one of
    its packages quietly auto-released underneath it.
    """
    return (
        package.delivery_status == OUT_FOR_DELIVERY
        and package.vendor_status != "cancelled"
        and order.status not in {"cancelled", "refunded"}
        and order.payment_status == "paid"
        and package.auto_release_at is not None
        and package.auto_release_at <= now
    )


def auto_release_package(db: Session, order_id, package_id) -> tuple[Order | None, set[uuid.UUID]]:
    """Re-validates eligibility *inside* the row lock immediately before
    acting, so a customer confirmation or problem report that lands in the
    same instant as this job never gets silently overwritten (Invariant 9)."""
    order = _lock_order_by_id(db, order_id)
    if not order:
        return None, set()

    package = next((p for p in order.packages if str(p.id) == str(package_id)), None)
    if package is None:
        return order, set()

    now = datetime.now(UTC)
    if not is_eligible_for_auto_release(order, package, now):
        return order, set()

    label = (
        package.store_name or f"Package {package.package_number}"
        if len(order.packages) > 1
        else f"order #{order.order_number}"
    )
    changed_wallet_vendor_ids = _complete_package(
        db,
        order,
        package,
        confirmation_method=CONFIRMATION_AUTO_RELEASE,
        actor_role="system",
        actor_id=None,
        note=f"Auto-confirmed {AUTO_RELEASE_GRACE_HOURS}h after dispatch — no customer response",
        event_type="AUTO_RELEASED",
    )
    package.auto_released_at = now
    if order.delivery_status == DELIVERED:
        order.auto_released_at = now

    _notify_customer_delivered(
        db,
        order,
        title="Delivery marked complete",
        body=(
            f"We didn't hear back about {label}, so we've marked it delivered. "
            "Contact support if that's not right."
        ),
    )
    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    logger.info("delivery.auto_released order_id=%s package_id=%s", order.id, package.id)
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
