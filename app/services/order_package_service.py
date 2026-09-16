"""Building an order's packages, and rolling their state back up to the order.

An order is one commercial event, several fulfilment ones. The packages own
the fulfilment truth -- each vendor's stage, clock and settlement -- and this
module keeps `Order.vendor_status`, `Order.delivery_status`, `Order.progress`
and the delivery timestamps as a faithful *summary* of them.

Keeping the roll-up is not sentimentality about old columns. Admin queries,
analytics, the customer's order list, push copy and half a dozen indexes all
read those fields today. Deriving them instead of deleting them means the
package split is invisible to every reader that only cares whether the order
as a whole has shipped, while the readers that need per-vendor truth ask the
packages.

The summary rule for status is **the least advanced package wins**. An order
is only "ready" once every shop is ready; it is "out for delivery" the moment
one shop has dispatched *and* none are still packing. That is the reading a
customer would give the words themselves, and it is the one that stops a fast
vendor speaking for a slow one -- which is precisely the bug the packages
exist to end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, OrderPackage, Product, Store
from app.services.finance_math import round_money
from app.services.package_pricing_service import PackageGroup, PackageQuote

#: Vendor fulfilment stages, least to most advanced. Index order is the whole
#: definition of "least advanced" used by the roll-up below.
VENDOR_STAGE_ORDER = (
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
)

#: What the customer's progress bar reads at each stage.
VENDOR_STAGE_PROGRESS = {
    "pending": 0.1,
    "confirmed": 0.2,
    "processing": 0.45,
    "ready": 0.75,
    "out_for_delivery": 0.9,
    "delivered": 1.0,
    "cancelled": 0.0,
}

# Delivery sub-states, mirroring delivery_lifecycle_service.
NOT_DISPATCHED = "not_dispatched"
OUT_FOR_DELIVERY = "out_for_delivery"
RESCHEDULED = "rescheduled"
CUSTOMER_PROBLEM = "customer_problem"
DELIVERED = "delivered"
FAILED = "failed"


# --------------------------------------------------------------------------
# Grouping a cart into packages
# --------------------------------------------------------------------------


def group_order_items(db: Session, order: Order) -> list[PackageGroup]:
    """One group per vendor on the order, in the order their first item was
    added -- which is what gives packages a stable, meaningful numbering
    ("Package 1" is the first thing they put in the basket).

    Items with no `vendor_user_id` are skipped, for the same reason settlement
    has always skipped them: there is no vendor to fulfil or to pay.
    """
    ordered_vendor_ids: list[uuid.UUID] = []
    subtotals: dict[uuid.UUID, float] = {}
    store_ids: dict[uuid.UUID, str | None] = {}

    for item in order.items:
        vendor_user_id = item.vendor_user_id
        if not vendor_user_id:
            continue
        if vendor_user_id not in subtotals:
            ordered_vendor_ids.append(vendor_user_id)
            subtotals[vendor_user_id] = 0.0
            store_ids[vendor_user_id] = item.store_id
        subtotals[vendor_user_id] += float(item.line_total)
        if store_ids[vendor_user_id] is None:
            store_ids[vendor_user_id] = item.store_id

    stores = _load_stores(db, [sid for sid in store_ids.values() if sid])
    return [
        PackageGroup(
            vendor_user_id=vendor_user_id,
            store_id=store_ids[vendor_user_id],
            store_name=(
                stores[store_ids[vendor_user_id]].title
                if store_ids[vendor_user_id] in stores
                else None
            ),
            store=stores.get(store_ids[vendor_user_id] or ""),
            items_subtotal=round_money(subtotals[vendor_user_id]),
        )
        for vendor_user_id in ordered_vendor_ids
    ]


def group_checkout_items(
    db: Session,
    items,
    product_snapshot_map: dict,
) -> list[PackageGroup]:
    """The same grouping, but from a checkout payload before the Order exists.

    Used to price delivery at quote time, so the customer sees the real
    per-shop total before they commit rather than a flat guess that changes
    when the order is written.
    """
    ordered_vendor_ids: list[uuid.UUID] = []
    subtotals: dict[uuid.UUID, float] = {}
    store_ids: dict[uuid.UUID, str | None] = {}

    for item in items:
        snapshot = product_snapshot_map.get(item.product_id) or {}
        vendor_user_id = snapshot.get("vendor_user_id")
        if not vendor_user_id:
            continue
        if vendor_user_id not in subtotals:
            ordered_vendor_ids.append(vendor_user_id)
            subtotals[vendor_user_id] = 0.0
            store_ids[vendor_user_id] = snapshot.get("store_id")
        subtotals[vendor_user_id] += float(item.unit_price) * int(item.quantity)
        if store_ids[vendor_user_id] is None:
            store_ids[vendor_user_id] = snapshot.get("store_id")

    stores = _load_stores(db, [sid for sid in store_ids.values() if sid])
    return [
        PackageGroup(
            vendor_user_id=vendor_user_id,
            store_id=store_ids[vendor_user_id],
            store_name=(
                stores[store_ids[vendor_user_id]].title
                if store_ids[vendor_user_id] in stores
                else None
            ),
            store=stores.get(store_ids[vendor_user_id] or ""),
            items_subtotal=round_money(subtotals[vendor_user_id]),
        )
        for vendor_user_id in ordered_vendor_ids
    ]


def _load_stores(db: Session, store_ids: list[str]) -> dict[str, Store]:
    if not store_ids:
        return {}
    rows = db.scalars(select(Store).where(Store.id.in_(set(store_ids)))).all()
    return {store.id: store for store in rows}


def group_products_for_cart(db: Session, product_ids: list[str]) -> dict:
    """Vendor/store snapshot for a set of products, in the shape
    `group_checkout_items` expects. Lets the delivery quote endpoint price a
    cart without duplicating the order controller's loader."""
    if not product_ids:
        return {}
    rows = db.execute(
        select(Product.id, Product.vendor_user_id, Product.store_id).where(
            Product.id.in_(set(product_ids))
        )
    ).all()
    return {
        product_id: {"vendor_user_id": vendor_user_id, "store_id": store_id}
        for product_id, vendor_user_id, store_id in rows
    }


# --------------------------------------------------------------------------
# Creating the package rows
# --------------------------------------------------------------------------


def build_packages_for_order(
    db: Session,
    order: Order,
    *,
    quotes: list[PackageQuote],
    discount_shares: dict[uuid.UUID, float] | None = None,
) -> list[OrderPackage]:
    """Attach one package per priced group to a freshly built order.

    Called once, at checkout, before the order is committed. `discount_shares`
    comes from `finance_math.vendor_allocation_map` so the split a package
    records is the same split settlement will later pay against -- one
    apportionment, computed once.
    """
    shares = discount_shares or {}
    packages: list[OrderPackage] = []
    for index, quote in enumerate(quotes, start=1):
        package = OrderPackage(
            order_id=order.id,
            vendor_user_id=quote.vendor_user_id,
            store_id=quote.store_id,
            store_name=quote.store_name,
            package_number=index,
            vendor_status="pending",
            delivery_status=NOT_DISPATCHED,
            items_subtotal=quote.items_subtotal,
            discount_share=round_money(shares.get(quote.vendor_user_id, 0.0)),
            delivery_fee=quote.delivery_fee,
            delivery_fee_waived=quote.fee_waived,
            settlement_status="not_eligible",
        )
        order.packages.append(package)
        packages.append(package)
    return packages


def package_for_vendor(
    db: Session, order: Order, vendor_user_id: uuid.UUID
) -> OrderPackage | None:
    for package in order.packages:
        if package.vendor_user_id == vendor_user_id:
            return package
    return None


def ensure_packages(db: Session, order: Order) -> list[OrderPackage]:
    """Packages for an order that predates them.

    The migration backfills every order that existed when it ran, so this is a
    safety net for the narrow window where an order was written by old code
    mid-deploy. It mirrors the backfill exactly: current order state copied
    onto each package, and **no delivery fee**, because that order was priced
    under the old rule where ODOS kept the shipping.
    """
    if order.packages:
        return list(order.packages)

    groups = group_order_items(db, order)
    if not groups:
        return []

    from app.services.finance_math import vendor_allocation_map

    allocations = vendor_allocation_map(order)
    for index, group in enumerate(groups, start=1):
        order.packages.append(
            OrderPackage(
                order_id=order.id,
                vendor_user_id=group.vendor_user_id,
                store_id=group.store_id,
                store_name=group.store_name,
                package_number=index,
                vendor_status=order.vendor_status,
                delivery_status=order.delivery_status,
                items_subtotal=group.items_subtotal,
                discount_share=round_money(
                    (allocations.get(group.vendor_user_id) or {}).get("discount_share", 0.0)
                ),
                delivery_fee=0.0,
                delivery_fee_waived=False,
                settlement_status=order.settlement_status,
                dispatched_at=order.dispatched_at,
                dispatch_attempt_count=order.dispatch_attempt_count,
                delivered_at=order.delivered_at,
                cancelled_at=order.cancelled_at,
                confirmation_method=order.confirmation_method,
                auto_release_at=order.auto_release_at,
                auto_released_at=order.auto_released_at,
                delivery_reminder_sent_at=order.delivery_reminder_sent_at,
                dispatch_photo_url=order.dispatch_photo_url,
                dispatch_photo_key=order.dispatch_photo_key,
                departure_notified_at=order.departure_notified_at,
                tracking_eta=order.tracking_eta,
            )
        )
    db.flush()
    return list(order.packages)


# --------------------------------------------------------------------------
# Rolling package state back up to the order
# --------------------------------------------------------------------------


def _least_advanced(stages: list[str]) -> str:
    known = [s for s in stages if s in VENDOR_STAGE_ORDER]
    if not known:
        return "pending"
    return min(known, key=VENDOR_STAGE_ORDER.index)


def rollup_vendor_status(packages: list[OrderPackage]) -> str:
    """The order's headline stage: the least advanced package that is still
    live. Cancelled packages are ignored unless every one of them is cancelled,
    in which case the order itself is cancelled."""
    live = [p for p in packages if p.vendor_status != "cancelled"]
    if not live:
        return "cancelled"
    return _least_advanced([p.vendor_status for p in live])


def rollup_delivery_status(packages: list[OrderPackage]) -> str:
    """The order's delivery headline.

    Order matters here. A problem or a failure anywhere is what the customer
    and admin need to see first, even when the rest of the order sailed
    through -- a summary that hides the one bag that went wrong is worse than
    no summary.
    """
    live = [p for p in packages if p.vendor_status != "cancelled"]
    if not live:
        return NOT_DISPATCHED

    statuses = {p.delivery_status for p in live}
    if CUSTOMER_PROBLEM in statuses:
        return CUSTOMER_PROBLEM
    if FAILED in statuses and statuses <= {FAILED, DELIVERED}:
        return FAILED
    if RESCHEDULED in statuses:
        return RESCHEDULED
    if statuses == {DELIVERED}:
        return DELIVERED
    if OUT_FOR_DELIVERY in statuses or DELIVERED in statuses:
        return OUT_FOR_DELIVERY
    return NOT_DISPATCHED


def rollup_settlement_status(packages: list[OrderPackage]) -> str:
    live = [p for p in packages if p.vendor_status != "cancelled"]
    if not live:
        return "not_eligible"
    statuses = {p.settlement_status for p in live}
    if "held" in statuses:
        return "held"
    if statuses == {"settled"}:
        return "settled"
    if "eligible" in statuses or "settled" in statuses:
        return "eligible"
    return "not_eligible"


def recompute_order_rollup(order: Order) -> None:
    """Refresh every derived field on the order from its packages.

    Call after any change to a package. Cheap, idempotent, and the single
    place the derivation lives -- so a new caller cannot invent its own
    slightly different idea of what the order's status means.
    """
    packages = list(order.packages)
    if not packages:
        return

    live = [p for p in packages if p.vendor_status != "cancelled"]

    order.vendor_status = rollup_vendor_status(packages)
    order.delivery_status = rollup_delivery_status(packages)
    order.settlement_status = rollup_settlement_status(packages)

    # Progress is the mean across live packages, so a three-shop order where
    # two have shipped reads as genuinely further along than one where none
    # have -- which "least advanced" alone would flatten to the same bar.
    if live:
        order.progress = round(
            sum(VENDOR_STAGE_PROGRESS.get(p.vendor_status, 0.0) for p in live) / len(live), 2
        )
    else:
        order.progress = 0.0

    # Earliest dispatch, latest delivery: the window the order was in motion.
    dispatch_times = [p.dispatched_at for p in live if p.dispatched_at]
    order.dispatched_at = min(dispatch_times) if dispatch_times else None
    order.dispatch_attempt_count = sum(p.dispatch_attempt_count for p in live)

    if order.delivery_status == DELIVERED:
        delivered_times = [p.delivered_at for p in live if p.delivered_at]
        order.delivered_at = max(delivered_times) if delivered_times else datetime.now(UTC)
    elif order.status != "cancelled":
        order.delivered_at = None

    # The order's auto-release clock is the soonest one still outstanding, so
    # any legacy reader of Order.auto_release_at still sees a sane value. The
    # sweep itself now reads the packages directly.
    pending_releases = [
        p.auto_release_at
        for p in live
        if p.auto_release_at and p.delivery_status == OUT_FOR_DELIVERY
    ]
    order.auto_release_at = min(pending_releases) if pending_releases else None

    problem = next((p for p in live if p.delivery_status == CUSTOMER_PROBLEM), None)
    order.delivery_problem_reason = problem.delivery_problem_reason if problem else None
    order.delivery_problem_reported_at = (
        problem.delivery_problem_reported_at if problem else None
    )

    if order.vendor_status == "cancelled":
        order.status = "cancelled"
        cancel_times = [p.cancelled_at for p in packages if p.cancelled_at]
        order.cancelled_at = max(cancel_times) if cancel_times else datetime.now(UTC)
    elif order.status not in ("pending_payment", "refunded"):
        order.status = "processing"


def package_item_ids(order: Order, package: OrderPackage) -> list[uuid.UUID]:
    return [
        item.id for item in order.items if item.vendor_user_id == package.vendor_user_id
    ]


def package_items(order: Order, package: OrderPackage) -> list[OrderItem]:
    return [
        item for item in order.items if item.vendor_user_id == package.vendor_user_id
    ]
