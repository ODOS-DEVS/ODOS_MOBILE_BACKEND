"""Helpers used by more than one admin domain.

Kept here rather than in any one domain module so the domains do not have to
import from each other, which would reintroduce the tangle the split exists to
remove.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

# Re-exported, not used in this module: the admin domain modules import the
# guard from here so shared pieces have one import path. ruff removed it once
# as unused, which broke collection for every module importing through it.
from app.core.auth import require_admin  # noqa: F401
from app.core.catalog_taxonomy import ODOS_CATEGORY_TAXONOMY
from app.models import (
    Order,
    Product,
    Store,
    User,
    UserRole,
)
from app.schemas.admin import AdminStoreProductRead

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


def _slugify(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)[:80]


def _normalize_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [value.strip() for value in values if value and value.strip()]
    return cleaned or None


def _build_discount(price: int, old_price: int | None) -> str | None:
    if old_price is None or old_price <= 0 or old_price <= price:
        return None

    percentage = round(((old_price - price) / old_price) * 100)
    return f"{percentage}% off"


def _taxonomy_lookup_by_slug() -> dict[str, dict]:
    return {entry["slug"]: entry for entry in ODOS_CATEGORY_TAXONOMY}


def _infer_image_key(category: str) -> str:
    normalized = category.strip().lower()
    if "bag" in normalized:
        return "bag"
    if "shoe" in normalized or "sandal" in normalized or "slipper" in normalized:
        return "shoe5"
    if "dress" in normalized or "fashion" in normalized or "clothing" in normalized:
        return "dress"
    if "men" in normalized or "gents" in normalized:
        return "gents"
    if "beauty" in normalized or "cosmetic" in normalized:
        return "cosmetics"
    if "sport" in normalized:
        return "sports"
    return "bag"


def _ensure_platform_store(db: Session) -> Store:
    admin_avatar_url = db.scalar(
        select(User.avatar_url)
        .where(
            User.role == UserRole.ADMIN,
            User.avatar_url.is_not(None),
        )
        .order_by(User.updated_at.desc())
        .limit(1)
    )
    existing = db.scalar(select(Store).where(Store.slug == "odos-official"))
    if existing:
        if admin_avatar_url:
            _sync_platform_store_avatar(existing, admin_avatar_url)
        return existing

    store = Store(
        id=_generate_store_id(),
        slug="odos-official",
        title="ODOS Official",
        category="Marketplace",
        market_id=None,
        market_slug=None,
        image_key="bag",
        image_url=admin_avatar_url,
        rating=4.8,
        address="ODOS Marketplace",
        phone=None,
        email="support@odos.app",
        city="Accra",
        region="Greater Accra",
        distance_km=None,
        travel_minutes=None,
        description="Platform-managed catalog products curated by ODOS.",
        image_banner_key=None,
        image_banner_url=admin_avatar_url,
        status="active",
        vendor_user_id=None,
        sort_order=0,
        is_active=True,
    )
    db.add(store)
    db.flush()
    return store


def _generate_store_id() -> str:
    return f"store-{uuid.uuid4().hex[:10]}"


def _sync_platform_store_avatar(store: Store, avatar_url: str | None) -> None:
    if avatar_url:
        store.image_url = avatar_url
        store.image_banner_url = avatar_url


def _serialize_store_product(product: Product) -> AdminStoreProductRead:
    return AdminStoreProductRead(
        id=product.id,
        name=product.title,
        status=product.status,
        price=product.price,
        old_price=product.old_price,
        discount=product.discount,
        stock=product.stock,
        category=product.category or "",
        subcategory=product.subcategory,
        images=product.image_urls or ([product.image_url] if product.image_url else []),
        created_at=product.created_at,
        updated_at=product.updated_at,
    )
