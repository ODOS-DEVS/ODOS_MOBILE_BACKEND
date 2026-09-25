"""Helpers used by more than one admin domain.

Kept here rather than in any one domain module so the domains do not have to
import from each other, which would reintroduce the tangle the split exists to
remove.
"""


# Re-exported, not used here: every admin domain module imports the guard
# from this module so there is one import path for shared pieces. ruff
# removed it once as "unused", which broke collection for the domain
# modules, hence the explicit marker.
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_admin  # noqa: F401
from app.models import (
    Order,
    Product,
    Store,
)

SUPPORTED_ACCOUNT_STATUSES = {"active", "blocked", "inactive"}
SUPPORTED_VENDOR_STATUSES = {"active", "suspended"}
SUPPORTED_STORE_STATUSES = {"active", "suspended", "draft"}
SUPPORTED_PRODUCT_STATUSES = {"pending", "active", "hidden", "suspended"}
SUPPORTED_ORDER_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
}


def _payment_status(order: Order) -> str:
    return order.payment_status


def _store_name_lookup(db: Session, order: Order) -> str:
    product_ids = [item.product_id for item in order.items]
    if not product_ids:
        return "Marketplace"

    store_ids = list(
        db.scalars(select(Product.store_id).where(Product.id.in_(product_ids))).all()
    )
    first_store_id = next((store_id for store_id in store_ids if store_id), None)
    if not first_store_id:
        return "Marketplace"

    store = db.scalar(select(Store).where(Store.id == first_store_id))
    return store.title if store else "Marketplace"
