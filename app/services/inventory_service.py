"""Stock visibility and vendor inventory alerts.

Customer catalog only shows active products with stock > 0.
When stock hits 0, products are auto-marked out_of_stock until the vendor
restocks and republishes.
"""

from __future__ import annotations

import logging

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.controllers.notification_controller import create_notification_event
from app.models import Product, User
from app.services.push_service import build_push_data, send_expo_push_notification

logger = logging.getLogger(__name__)

LOW_STOCK_THRESHOLD = 2
OUT_OF_STOCK_STATUS = "out_of_stock"


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
        previous_stock > LOW_STOCK_THRESHOLD
        and 0 < new_stock <= LOW_STOCK_THRESHOLD
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
