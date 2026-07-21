"""Stock visibility, vendor inventory alerts, and stock movement ledger.

Customer catalog only shows active products with stock > 0.
When stock hits 0, products are auto-marked out_of_stock until the vendor
restocks and republishes.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.controllers.notification_controller import create_notification_event
from app.models import InventoryMovement, Order, OrderItem, Product, User
from app.models.inventory import INVENTORY_MOVEMENT_REASONS
from app.services.push_service import build_push_data, send_expo_push_notification

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

LOW_STOCK_THRESHOLD = 2
OUT_OF_STOCK_STATUS = "out_of_stock"

# Order statuses that still hold inventory for fulfillment.
OPEN_ORDER_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
}


def customer_visible_product_filters():
    """SQLAlchemy filters for shopper-facing catalog surfaces."""
    return and_(
        Product.is_active.is_(True),
        Product.status == "active",
        Product.stock > 0,
    )


def mark_product_out_of_stock(product: Product) -> bool:
    """Hide a zero-stock product from the shopper app. Returns True if changed."""
    if product.stock > 0:
        return False
    # Preserve admin suspension and pending review states.
    if product.status in {"suspended", "pending"}:
        return False
    if product.status == OUT_OF_STOCK_STATUS and not product.is_active:
        return False
    product.status = OUT_OF_STOCK_STATUS
    product.is_active = False
    return True


def _load_vendor_user(db: Session, product: Product) -> User | None:
    if not product.vendor_user_id:
        return None
    return db.get(User, product.vendor_user_id)


def _vendor_wants_inventory_notify(vendor: User) -> bool:
    if hasattr(vendor, "vendor_notify_inventory"):
        return bool(getattr(vendor, "vendor_notify_inventory", True))
    return True


def _notify_vendor_inventory(
    db: Session,
    product: Product,
    *,
    kind: str,
    title: str,
    body: str,
) -> None:
    vendor = _load_vendor_user(db, product)
    if not vendor:
        return
    if not _vendor_wants_inventory_notify(vendor):
        return

    try:
        event = create_notification_event(
            db,
            vendor,
            kind=kind,
            title=title,
            body=body,
            icon="bag-handle-outline",
            accent="warning",
            action_label="Restock",
            route_type="vendor_product",
            route_target_id=product.id,
            image_key=product.image_key,
            image_url=product.image_url,
        )
    except Exception:
        logger.exception("Failed to create inventory notification for %s", product.id)
        event = None

    try:
        if vendor.expo_push_token and vendor.allow_notifications:
            send_expo_push_notification(
                user=vendor,
                title=title,
                body=body,
                data=build_push_data(
                    push_type=kind,
                    route_type="vendor_product",
                    route_target_id=product.id,
                    notification_event=event,
                    extra={
                        "productId": product.id,
                        "stock": product.stock,
                    },
                ),
                channel_id="vendor-inventory",
                sound="default",
            )
    except Exception:
        logger.exception("Failed to send inventory push for product %s", product.id)


def apply_inventory_side_effects(
    db: Session,
    product: Product,
    *,
    previous_stock: int,
) -> bool:
    """Apply hide + alerts after a stock change.

    Returns True when the product visibility/status changed and callers should
    broadcast a catalog update.
    """
    new_stock = max(int(product.stock or 0), 0)
    product.stock = new_stock
    visibility_changed = False

    crossed_to_zero = previous_stock > 0 and new_stock <= 0
    crossed_to_low = (
        previous_stock > LOW_STOCK_THRESHOLD and 0 < new_stock <= LOW_STOCK_THRESHOLD
    )

    if crossed_to_zero:
        visibility_changed = mark_product_out_of_stock(product) or visibility_changed
        _notify_vendor_inventory(
            db,
            product,
            kind="vendor_out_of_stock",
            title="Out of stock",
            body=(
                f"{product.title} is sold out and hidden from shoppers. "
                "Restock and republish it to sell again."
            ),
        )
    elif crossed_to_low:
        _notify_vendor_inventory(
            db,
            product,
            kind="vendor_low_stock",
            title="Low stock",
            body=f"Only {new_stock} left for {product.title}. Restock soon to avoid going offline.",
        )

    # If stock was already 0 but status was still live, heal visibility.
    if new_stock <= 0 and product.status == "active":
        visibility_changed = mark_product_out_of_stock(product) or visibility_changed

    return visibility_changed


def record_stock_change(
    db: Session,
    product: Product,
    *,
    new_stock: int,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    note: str | None = None,
    actor: User | None = None,
) -> bool:
    """Set product stock, append an inventory ledger row, and run side effects.

    Returns True when catalog visibility/status changed.
    """
    if reason not in INVENTORY_MOVEMENT_REASONS:
        raise ValueError(f"Unsupported inventory movement reason: {reason}")

    previous_stock = max(int(product.stock or 0), 0)
    resolved_stock = max(int(new_stock), 0)
    delta = resolved_stock - previous_stock

    product.stock = resolved_stock

    if delta != 0 or reason in {"system", "manual", "bulk"}:
        # Always ledger non-zero deltas; also ledger explicit manual/bulk/system
        # when stock is set to the same value only if delta != 0.
        if delta != 0:
            db.add(
                InventoryMovement(
                    id=uuid.uuid4(),
                    product_id=product.id,
                    vendor_user_id=product.vendor_user_id,
                    store_id=product.store_id,
                    delta=delta,
                    stock_after=resolved_stock,
                    reason=reason,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    note=(note or None),
                    created_by_user_id=actor.id if actor else None,
                )
            )

    return apply_inventory_side_effects(db, product, previous_stock=previous_stock)


def compute_reserved_stock_map(
    db: Session,
    product_ids: list[str],
) -> dict[str, int]:
    """Sum quantities on open orders for each product id."""
    if not product_ids:
        return {}

    rows = db.execute(
        select(OrderItem.product_id, func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            OrderItem.product_id.in_(product_ids),
            Order.status.in_(OPEN_ORDER_STATUSES),
        )
        .group_by(OrderItem.product_id)
    ).all()

    return {str(product_id): int(total or 0) for product_id, total in rows}


def available_stock(on_hand: int, reserved: int) -> int:
    return max(0, int(on_hand or 0) - int(reserved or 0))
