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
    PaymentTransaction,
    Product,
    ReturnRequest,
    Review,
    Store,
    User,
    UserRole,
)
from app.schemas.admin import (
    AdminOrderRead,
    AdminReturnRequestRead,
    AdminReviewRead,
    AdminStoreProductRead,
    AdminUserStoreSummaryRead,
)
from app.schemas.payment import (
    AdminPaymentTransactionRead,
)
from app.services.finance_math import amount_from_subunit, round_money

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


def _resolve_review_context(
    db: Session,
    reviews: list[Review],
) -> tuple[dict[str, Product], dict[str, str]]:
    product_ids = list({review.product_id for review in reviews})
    if not product_ids:
        return {}, {}

    products = {
        product.id: product
        for product in db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    }
    store_ids = [product.store_id for product in products.values() if product.store_id]
    if not store_ids:
        return products, {}

    store_name_map = dict(db.execute(
            select(Store.id, Store.title).where(Store.id.in_(store_ids))
        ).all())
    return products, store_name_map


def _serialize_order(db: Session, order: Order) -> AdminOrderRead:
    return AdminOrderRead(
        id=order.id,
        order_number=order.order_number,
        customer_name=order.address_full_name,
        store_name=_store_name_lookup(db, order),
        total_amount=round(order.total_amount, 2),
        status=order.vendor_status or order.status,
        payment_status=_payment_status(order),
        created_at=order.created_at,
    )


def _build_admin_review_read(
    review: Review,
    *,
    products: dict[str, Product],
    store_name_map: dict[str, str],
) -> AdminReviewRead:
    product = products.get(review.product_id)
    order_item = next(
        (item for item in review.order.items if item.product_id == review.product_id),
        None,
    )
    product_name = product.title if product else order_item.title if order_item else review.product_id
    store_name = store_name_map.get(product.store_id or "") if product else None
    return _serialize_admin_review(
        review,
        product_name=product_name,
        store_name=store_name,
    )


def _serialize_return_request(db: Session, request: ReturnRequest) -> AdminReturnRequestRead:
    order = request.order
    order_item = request.order_item
    reviewed_by = request.reviewed_by_user
    return AdminReturnRequestRead(
        id=request.id,
        order_id=request.order_id,
        order_number=order.order_number,
        order_item_id=request.order_item_id,
        product_id=order_item.product_id,
        product_title=order_item.title,
        product_image_url=order_item.image_url,
        product_image_key=order_item.image_key,
        selected_color=order_item.selected_color,
        selected_size=order_item.selected_size,
        store_name=_store_name_lookup(db, order),
        user_id=request.user_id,
        customer_name=order.address_full_name,
        customer_email=order.user.email,
        request_type=request.request_type,
        status=request.status,
        quantity=request.quantity,
        reason=request.reason,
        details=request.details,
        evidence_image_urls=request.evidence_image_urls,
        admin_note=request.admin_note,
        refund_amount=round(request.refund_amount, 2) if request.refund_amount is not None else None,
        reviewed_by_user_id=request.reviewed_by_user_id,
        reviewed_by_name=reviewed_by.full_name if reviewed_by else None,
        reviewed_at=request.reviewed_at,
        resolved_at=request.resolved_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def _serialize_user_payment_transaction(transaction: PaymentTransaction) -> AdminPaymentTransactionRead:
    order = transaction.order
    user = transaction.user
    return AdminPaymentTransactionRead(
        id=transaction.id,
        order_id=transaction.order_id,
        order_number=order.order_number if order else "",
        user_id=transaction.user_id,
        customer_email=user.email if user else "",
        provider=transaction.provider,
        reference=transaction.reference,
        amount=round_money(order.total_amount if order else 0),
        currency=transaction.currency,
        status=transaction.status,
        preferred_channel=transaction.preferred_channel,
        processor_fee_amount=amount_from_subunit(transaction.processor_fee_subunit),
        gateway_response=transaction.gateway_response,
        provider_transaction_id=transaction.provider_transaction_id,
        paid_at=transaction.paid_at,
        verified_at=transaction.verified_at,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
    )


def _serialize_user_store_summary(store: Store) -> AdminUserStoreSummaryRead:
    return AdminUserStoreSummaryRead(
        id=store.id,
        name=store.title,
        slug=store.slug,
        status=store.status,
        logo_image=store.image_url,
        banner_image=store.image_banner_url,
        market_id=store.market_id,
        location=store.address,
        region=store.region or "",
        city=store.city or "",
        created_at=store.created_at,
        updated_at=store.updated_at,
    )


def _serialize_admin_review(
    review: Review,
    *,
    product_name: str,
    store_name: str | None,
) -> AdminReviewRead:
    return AdminReviewRead(
        id=review.id,
        order_id=review.order_id,
        order_number=review.order.order_number,
        product_id=review.product_id,
        product_name=product_name,
        store_name=store_name,
        user_id=review.user_id,
        user_name=review.user.full_name,
        user_email=review.user.email,
        rating=review.rating,
        comment=review.comment,
        vendor_reply=review.vendor_reply,
        vendor_replied_at=review.vendor_replied_at,
        is_hidden=review.is_hidden,
        moderation_reason=review.moderation_reason,
        moderated_at=review.moderated_at,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )
