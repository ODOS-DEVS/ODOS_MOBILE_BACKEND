from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.promo_banner_config import (
    PROMO_CAMPAIGN_TAGS,
    describe_promo_destination,
    normalize_promo_link_type,
    normalize_promo_placement,
)
from app.controllers.auth_controller import build_auth_token, login_user
from app.core.admin_pagination import paginate_scalars
from app.schemas.pagination import AdminPageRead
from app.controllers.finance_controller import (
    get_admin_finance_overview,
    list_admin_payment_transactions,
    list_admin_platform_ledger_entries,
    record_refund_adjustments,
)
from app.controllers.notification_controller import create_notification_event
from app.core.security import hash_password
from app.core.catalog_taxonomy import ODOS_CATEGORY_TAXONOMY
from app.controllers.vendor_controller import (
    approve_vendor_application,
    broadcast_catalog_product_change,
    broadcast_catalog_store_change,
    fetch_vendor_dashboard,
    list_vendor_applications,
    reject_vendor_application,
    serialize_vendor_product,
)
from app.controllers.review_controller import recompute_product_review_metrics
from app.controllers.wallet_controller import (
    list_admin_vendor_withdrawal_requests,
    publish_vendor_wallet_updates,
    reverse_vendor_wallet_for_return_request,
    settle_vendor_wallets_for_order,
    update_admin_vendor_withdrawal_request,
)
from app.controllers.voucher_controller import (
    SUPPORTED_VOUCHER_DISCOUNT_TYPES,
    build_voucher_reward_text,
    validate_voucher_configuration,
    voucher_status,
)
from app.services.media_service import remove_media_file, save_image_upload, save_image_uploads
from app.services.realtime_service import realtime_manager
from app.models import (
    CartItem,
    Category,
    CustomerWallet,
    Market,
    NotificationEvent,
    NotificationRead,
    Order,
    OrderItem,
    PaymentTransaction,
    Product,
    PromoBanner,
    FlashSaleEvent,
    FlashSaleEventProduct,
    ReturnRequest,
    Review,
    SavedAddress,
    SavedPaymentMethod,
    Store,
    User,
    UserAuthAccount,
    UserRole,
    VendorApplication,
    VendorStatus,
    Voucher,
    VoucherRedemption,
    WishlistItem,
)
from app.models.user_behavior import UserBehaviorEvent
from app.schemas.admin import (
    AdminBootstrapStatusRead,
    AdminCategoryRead,
    AdminCategoryUpsert,
    AdminDashboardRead,
    AdminDashboardStatsRead,
    AdminMarketRead,
    AdminMarketUpsert,
    AdminNotificationRead,
    AdminOrderDetailRead,
    AdminOrderItemRead,
    AdminOrderRead,
    AdminOrderStatusUpdate,
    AdminReturnRequestRead,
    AdminReturnRequestUpdate,
    AdminVendorWithdrawalRequestRead,
    AdminVendorWithdrawalUpdate,
    AdminProductCreate,
    AdminProductRead,
    AdminProductStatusUpdate,
    AdminPromoBannerRead,
    AdminPromoBannerUpsert,
    AdminFlashSaleEventRead,
    AdminFlashSaleEventUpsert,
    AdminUserAddressRead,
    AdminUserCartItemRead,
    AdminUserDetailRead,
    AdminUserNotificationRead,
    AdminUserPaymentMethodRead,
    AdminUserWalletSummaryRead,
    AdminUserWishlistItemRead,
    AdminReviewModerationUpdate,
    AdminReviewRead,
    AdminStoreDetailRead,
    AdminStoreProductRead,
    AdminStoreRead,
    AdminStoreStatsRead,
    AdminUserStatsRead,
    AdminStoreUpsert,
    AdminStoreStatusUpdate,
    AdminUserStoreSummaryRead,
    AdminUserRead,
    AdminUserStatusUpdate,
    AdminUserVendorApplicationRead,
    AdminVendorRead,
    AdminVendorStatusUpdate,
    AdminVoucherRead,
    AdminVoucherReview,
    AdminVoucherUpsert,
    NotificationMarkReadResponse,
)
from app.schemas.payment import (
    AdminFinanceOverviewRead,
    AdminPaymentTransactionRead,
    AdminPlatformLedgerEntryRead,
)
from app.schemas.user import AuthToken, UserCreate, UserLogin
from app.services.delivery_service import delivery_method_label, get_delivery_config
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
SUPPORTED_RETURN_REQUEST_STATUSES = {
    "requested",
    "under_review",
    "approved",
    "rejected",
    "refunded",
    "exchanged",
}


def _slugify(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)[:80]


def _normalize_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [value.strip() for value in values if value and value.strip()]
    return cleaned or None


def _generate_store_id() -> str:
    return f"store-{uuid.uuid4().hex[:10]}"


def _generate_product_id() -> str:
    return f"admin-product-{uuid.uuid4().hex[:12]}"


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )


def _account_status(user: User) -> str:
    return "active" if user.is_active else "blocked"


def _notification_type(kind: str) -> str:
    normalized = kind.lower()
    if "vendor" in normalized:
        return "vendor"
    if "order" in normalized:
        return "order"
    if "store" in normalized:
        return "store"
    if "user" in normalized or "account" in normalized:
        return "user"
    return "system"


def _payment_status(order: Order) -> str:
    return order.payment_status


def _voucher_status(voucher: Voucher, redemption_count: int) -> str:
    now = datetime.now(UTC)
    return voucher_status(voucher, now=now, overall_count=redemption_count)


def _serialize_voucher(
    voucher: Voucher,
    *,
    store_name: str | None = None,
    redemption_count: int = 0,
    unique_user_count: int = 0,
    total_discount_amount: float = 0,
) -> AdminVoucherRead:
    return AdminVoucherRead(
        id=voucher.id,
        code=voucher.code,
        title=voucher.title,
        description=voucher.description,
        issuer_name=voucher.issuer_name,
        scope=voucher.scope,
        availability=voucher.availability,
        store_id=voucher.store_id,
        store_name=store_name,
        reward_text=voucher.reward_text,
        discount_type=voucher.discount_type,
        discount_value=round(voucher.discount_value, 2),
        min_subtotal=round(voucher.min_subtotal, 2),
        max_discount=round(voucher.max_discount, 2) if voucher.max_discount is not None else None,
        usage_limit=voucher.usage_limit,
        per_user_limit=voucher.per_user_limit,
        is_active=voucher.is_active,
        status=_voucher_status(voucher, redemption_count),
        redemption_count=redemption_count,
        unique_user_count=unique_user_count,
        total_discount_amount=round(total_discount_amount, 2),
        starts_at=voucher.starts_at,
        ends_at=voucher.ends_at,
        created_at=voucher.created_at,
        approval_status=getattr(voucher, "approval_status", "approved"),
        campaign_tag=getattr(voucher, "campaign_tag", None),
        review_notes=getattr(voucher, "review_notes", None),
        created_by_user_id=getattr(voucher, "created_by_user_id", None),
    )


def _validate_voucher_payload(payload: AdminVoucherUpsert) -> None:
    validate_voucher_configuration(
        scope=payload.scope,
        availability=payload.availability,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        store_id=payload.store_id,
    )


def _voucher_stats_map(db: Session, voucher_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, float | int]]:
    if not voucher_ids:
        return {}

    rows = db.execute(
        select(
            VoucherRedemption.voucher_id,
            func.count(VoucherRedemption.id),
            func.count(func.distinct(VoucherRedemption.user_id)),
            func.coalesce(func.sum(VoucherRedemption.discount_amount), 0),
        )
        .where(VoucherRedemption.voucher_id.in_(voucher_ids))
        .group_by(VoucherRedemption.voucher_id)
    ).all()

    return {
        voucher_id: {
            "redemption_count": int(redemption_count or 0),
            "unique_user_count": int(unique_user_count or 0),
            "total_discount_amount": round(float(total_discount_amount or 0), 2),
        }
        for voucher_id, redemption_count, unique_user_count, total_discount_amount in rows
    }


def _serialize_user(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        avatar_url=user.avatar_url,
        roles=user.roles,
        vendor_status=user.vendor_status,
        account_status=_account_status(user),
        joined_at=user.created_at,
    )


def _serialize_saved_address(address: SavedAddress) -> AdminUserAddressRead:
    return AdminUserAddressRead(
        id=address.id,
        label=address.label,
        full_name=address.full_name,
        phone=address.phone,
        street=address.street,
        city=address.city,
        region=address.region,
        is_default=address.is_default,
        created_at=address.created_at,
        updated_at=address.updated_at,
    )


def _serialize_saved_payment_method(method: SavedPaymentMethod) -> AdminUserPaymentMethodRead:
    return AdminUserPaymentMethodRead(
        id=method.id,
        type=method.type.value if hasattr(method.type, "value") else str(method.type),
        label=method.label,
        is_default=method.is_default,
        card_name=method.card_name,
        card_last4=method.card_last4,
        expiry=method.expiry,
        network=method.network,
        phone=method.phone,
        created_at=method.created_at,
        updated_at=method.updated_at,
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


def _serialize_vendor_application_detail(
    application: VendorApplication,
) -> AdminUserVendorApplicationRead:
    return AdminUserVendorApplicationRead(
        id=application.id,
        status=application.status,
        business_name=application.business_name,
        business_category=application.business_category,
        business_description=application.business_description,
        phone_number=application.phone_number,
        whatsapp_number=application.whatsapp_number,
        region=application.region,
        city=application.city,
        market_id=application.market_id,
        store_location=application.store_location,
        store_name=application.store_name,
        store_description=application.store_description,
        ghana_card_number=application.ghana_card_number,
        business_registration_number=application.business_registration_number,
        logo_image_url=application.logo_image_url,
        banner_image_url=application.banner_image_url,
        shop_image_url=application.shop_image_url,
        rejection_reason=application.rejection_reason,
        reviewed_at=application.reviewed_at,
        submitted_at=application.submitted_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
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


def _serialize_user_detail(db: Session, user: User) -> AdminUserDetailRead:
    stores = list(
        db.scalars(select(Store).where(Store.vendor_user_id == user.id).order_by(Store.created_at.desc())).all()
    )
    orders = sorted(user.orders, key=lambda order: order.created_at, reverse=True)
    reviews = sorted(user.reviews, key=lambda review: review.updated_at, reverse=True)
    return_requests = sorted(user.return_requests, key=lambda request: request.created_at, reverse=True)
    payment_transactions = sorted(
        user.payment_transactions,
        key=lambda transaction: transaction.created_at,
        reverse=True,
    )
    cart_items = sorted(user.cart_items, key=lambda item: item.updated_at, reverse=True)
    wishlist_items = sorted(user.wishlist_items, key=lambda item: item.created_at, reverse=True)
    notifications = sorted(user.notification_events, key=lambda event: event.created_at, reverse=True)
    review_products, review_store_name_map = _resolve_review_context(db, reviews)
    behavior_event_count = db.scalar(
        select(func.count()).select_from(UserBehaviorEvent).where(UserBehaviorEvent.user_id == user.id)
    ) or 0
    customer_wallet = user.customer_wallet
    wallet_summary = (
        AdminUserWalletSummaryRead(
            balance=round_money(customer_wallet.available_balance),
            currency=customer_wallet.currency,
            lifetime_topups=round_money(customer_wallet.lifetime_topups),
            lifetime_spend=round_money(customer_wallet.lifetime_spend),
            lifetime_refunds=round_money(customer_wallet.lifetime_refunds),
            transaction_count=len(customer_wallet.transactions),
        )
        if customer_wallet
        else None
    )

    return AdminUserDetailRead(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        avatar_url=user.avatar_url,
        roles=user.roles,
        vendor_status=user.vendor_status,
        account_status=_account_status(user),
        joined_at=user.created_at,
        date_of_birth=user.date_of_birth,
        gender=user.gender,
        city=user.city,
        region=user.region,
        allow_notifications=user.allow_notifications,
        discount_notifications=user.discount_notifications,
        store_notifications=user.store_notifications,
        vendor_order_notifications=user.vendor_order_notifications,
        system_notifications=user.system_notifications,
        location_notifications=user.location_notifications,
        location_updates=user.location_updates,
        personalization_enabled=user.personalization_enabled,
        analytics_enabled=user.analytics_enabled,
        phone_verified=user.phone_verified,
        vendor_rejection_reason=user.vendor_rejection_reason,
        is_verified=user.is_verified,
        last_login_at=user.last_login_at,
        updated_at=user.updated_at,
        auth_providers=sorted({account.provider for account in user.auth_accounts if account.provider}),
        addresses=[
            _serialize_saved_address(address)
            for address in sorted(
                user.saved_addresses,
                key=lambda item: (not item.is_default, item.created_at),
            )
        ],
        payment_methods=[
            _serialize_saved_payment_method(method)
            for method in sorted(
                user.saved_payment_methods,
                key=lambda item: (not item.is_default, item.created_at),
            )
        ],
        vendor_application=_serialize_vendor_application_detail(user.vendor_application)
        if user.vendor_application
        else None,
        stores=[_serialize_user_store_summary(store) for store in stores],
        stats=AdminUserStatsRead(
            total_orders=len(user.orders),
            total_reviews=len(user.reviews),
            total_saved_addresses=len(user.saved_addresses),
            total_saved_payment_methods=len(user.saved_payment_methods),
            total_cart_items=len(user.cart_items),
            total_wishlist_items=len(user.wishlist_items),
            total_notifications=len(user.notification_events),
            total_spent=float(sum(order.total_amount for order in user.orders)),
            last_order_at=orders[0].created_at if orders else None,
            last_review_at=reviews[0].updated_at if reviews else None,
        ),
        orders=[_serialize_order(db, order) for order in orders],
        reviews=[
            _build_admin_review_read(
                review,
                products=review_products,
                store_name_map=review_store_name_map,
            )
            for review in reviews
        ],
        return_requests=[_serialize_return_request(db, request) for request in return_requests],
        payment_transactions=[
            _serialize_user_payment_transaction(transaction)
            for transaction in payment_transactions
            if transaction.order and transaction.user
        ],
        cart_items=[
            AdminUserCartItemRead(
                id=item.id,
                product_id=item.product_id,
                title=item.title,
                image_url=item.image_url,
                category=item.category,
                price=item.price,
                quantity=item.quantity,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in cart_items
        ],
        wishlist_items=[
            AdminUserWishlistItemRead(
                id=item.id,
                product_id=item.product_id,
                title=item.title,
                image_url=item.image_url,
                category=item.category,
                price=item.price,
                created_at=item.created_at,
            )
            for item in wishlist_items
        ],
        notifications=[
            AdminUserNotificationRead(
                id=event.id,
                kind=event.kind,
                title=event.title,
                message=event.body,
                created_at=event.created_at,
            )
            for event in notifications
        ],
        customer_wallet=wallet_summary,
        behavior_event_count=behavior_event_count,
    )


def _serialize_store(store: Store) -> AdminStoreRead:
    return AdminStoreRead(
        id=store.id,
        vendor_id=str(store.vendor_user_id) if store.vendor_user_id else None,
        name=store.title,
        slug=store.slug,
        description=store.description or "",
        category=store.category or "",
        audience_slugs=store.audience_slugs,
        market_id=store.market_id,
        location=store.address,
        region=store.region or "",
        city=store.city or "",
        banner_image=store.image_banner_url,
        logo_image=store.image_url,
        status=store.status,
        created_at=store.created_at,
    )


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


def _store_activity_summary(
    db: Session,
    products: list[Product],
) -> AdminStoreStatsRead:
    total_products = len(products)
    active_products = sum(1 for product in products if product.status == "active")
    pending_products = sum(1 for product in products if product.status == "pending")
    hidden_products = sum(1 for product in products if product.status in {"hidden", "suspended"})

    product_ids = {product.id for product in products}
    if not product_ids:
        return AdminStoreStatsRead(
            total_products=total_products,
            active_products=active_products,
            pending_products=pending_products,
            hidden_products=hidden_products,
            total_orders=0,
            total_sales=0.0,
        )

    orders = list(db.scalars(select(Order).options(selectinload(Order.items))).all())
    total_orders = 0
    total_sales = 0.0

    for order in orders:
        matching_items = [item for item in order.items if item.product_id in product_ids]
        if not matching_items:
            continue
        total_orders += 1
        if order.vendor_status in {"confirmed", "processing", "ready", "delivered"}:
            total_sales += sum(item.line_total for item in matching_items)

    return AdminStoreStatsRead(
        total_products=total_products,
        active_products=active_products,
        pending_products=pending_products,
        hidden_products=hidden_products,
        total_orders=total_orders,
        total_sales=round(total_sales, 2),
    )


def _serialize_store_detail(
    db: Session,
    store: Store,
    *,
    vendor: User | None = None,
    market: Market | None = None,
    products: list[Product],
) -> AdminStoreDetailRead:
    base = _serialize_store(store)
    return AdminStoreDetailRead(
        **base.model_dump(),
        vendor_name=vendor.full_name if vendor else None,
        vendor_email=vendor.email if vendor else None,
        vendor_phone_number=vendor.phone_number if vendor else None,
        market_name=market.title if market else None,
        updated_at=store.updated_at,
        products=[_serialize_store_product(product) for product in products],
        stats=_store_activity_summary(db, products),
    )


def _serialize_market(market: Market) -> AdminMarketRead:
    return AdminMarketRead(
        id=market.id,
        name=market.title,
        slug=market.slug,
        image=market.image_key,
        image_url=market.image_url,
        status="active" if market.is_active else "disabled",
        created_at=market.created_at,
    )


def _serialize_category(category: Category) -> AdminCategoryRead:
    return AdminCategoryRead(
        id=category.id,
        name=category.title,
        slug=category.slug,
        description=category.subtitle,
        image=category.image_key,
        image_url=category.image_url,
        subcategories=category.subcategories,
        status="active" if category.is_active else "disabled",
        created_at=category.created_at,
    )


def broadcast_catalog_market_change(market: Market) -> None:
    from app.core.cache import invalidate_catalog_markets

    invalidate_catalog_markets()
    realtime_manager.broadcast_event_sync(
        "catalog.market.changed",
        {
            "market_id": market.id,
            "slug": market.slug,
            "status": "active" if market.is_active else "disabled",
            "is_active": market.is_active,
        },
    )


def broadcast_catalog_category_change(category: Category) -> None:
    from app.core.cache import invalidate_catalog_categories, invalidate_catalog_products

    invalidate_catalog_categories()
    invalidate_catalog_products()
    realtime_manager.broadcast_event_sync(
        "catalog.category.changed",
        {
            "category_id": category.id,
            "slug": category.slug,
            "status": "active" if category.is_active else "disabled",
            "is_active": category.is_active,
        },
    )


def _serialize_product(
    product: Product,
    *,
    store: Store | None = None,
    vendor: User | None = None,
) -> AdminProductRead:
    return AdminProductRead(
        id=product.id,
        store_id=product.store_id,
        store_name=store.title if store else None,
        store_slug=store.slug if store else None,
        store_category=store.category if store else None,
        store_location=store.address if store else None,
        store_region=store.region if store else None,
        store_city=store.city if store else None,
        vendor_id=str(product.vendor_user_id) if product.vendor_user_id else None,
        vendor_name=vendor.full_name if vendor else None,
        vendor_email=vendor.email if vendor else None,
        name=product.title,
        description=product.description or "",
        images=product.image_urls or ([product.image_url] if product.image_url else []),
        image_key=product.image_key,
        category=product.category or "",
        subcategory=product.subcategory,
        category_slugs=product.category_slugs,
        subcategory_slugs=product.subcategory_slugs,
        audience_slug=product.audience_slug,
        section=product.section,
        placement_tags=product.placement_tags,
        price=product.price,
        old_price=product.old_price,
        discount=product.discount,
        rating=product.rating,
        reviews=product.reviews,
        color_options=product.color_options,
        size_options=product.size_options,
        specifications=product.specifications,
        stock=product.stock,
        status=product.status,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


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


def _serialize_order_item(item: OrderItem) -> AdminOrderItemRead:
    return AdminOrderItemRead(
        id=item.id,
        product_id=item.product_id,
        title=item.title,
        category=item.category,
        image_url=item.image_url,
        image_key=item.image_key,
        quantity=item.quantity,
        unit_price=round(item.unit_price, 2),
        line_total=round(item.line_total, 2),
        selected_color=item.selected_color,
        selected_size=item.selected_size,
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


def _serialize_order_detail(db: Session, order: Order) -> AdminOrderDetailRead:
    base = _serialize_order(db, order)
    delivery_config = get_delivery_config(db)
    method = order.delivery_method or "economy"
    return AdminOrderDetailRead(
        **base.model_dump(),
        customer_id=order.user_id,
        customer_email=order.user.email,
        customer_phone_number=order.user.phone_number,
        customer_avatar_url=order.user.avatar_url,
        source=order.source,
        internal_status=order.status,
        vendor_status=order.vendor_status,
        subtotal_amount=round(order.subtotal_amount, 2),
        shipping_amount=round(order.shipping_amount, 2),
        discount_amount=round(order.discount_amount, 2),
        delivery_method=method,
        delivery_method_label=delivery_method_label(method, delivery_config),
        progress=order.progress,
        tracking_eta=order.tracking_eta,
        cancellation_reason=order.cancellation_reason,
        address_full_name=order.address_full_name,
        address_phone=order.address_phone,
        address_street=order.address_street,
        address_city=order.address_city,
        address_region=order.address_region,
        payment_type=order.payment_type,
        payment_label=order.payment_label,
        payment_provider=order.payment_provider,
        payment_reference=order.payment_reference,
        payment_network=order.payment_network,
        payment_phone=order.payment_phone,
        payment_last4=order.payment_last4,
        voucher_id=order.voucher_id,
        voucher_code=order.voucher_code,
        voucher_title=order.voucher_title,
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        delivered_at=order.delivered_at,
        cancelled_at=order.cancelled_at,
        refunded_at=order.refunded_at,
        updated_at=order.updated_at,
        items=[_serialize_order_item(item) for item in order.items],
        return_requests=[_serialize_return_request(db, request) for request in order.return_requests],
    )


def _serialize_notification(notification: NotificationEvent, *, is_read: bool) -> AdminNotificationRead:
    return AdminNotificationRead(
        id=notification.id,
        type=_notification_type(notification.kind),
        title=notification.title,
        message=notification.body,
        read=is_read,
        created_at=notification.created_at,
    )


def _vendor_application_by_user(db: Session, user_id: uuid.UUID) -> VendorApplication | None:
    return db.scalar(select(VendorApplication).where(VendorApplication.user_id == user_id))


def _vendor_activity_summary(db: Session, vendor_user_id: uuid.UUID) -> tuple[int, int, int, float]:
    stores_count = db.scalar(
        select(func.count(Store.id)).where(Store.vendor_user_id == vendor_user_id)
    ) or 0
    products = list(
        db.scalars(select(Product).where(Product.vendor_user_id == vendor_user_id)).all()
    )
    product_ids = {product.id for product in products}
    if not product_ids:
        return stores_count, len(products), 0, 0.0

    orders = list(
        db.scalars(
            select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
        ).all()
    )

    total_orders = 0
    total_sales = 0.0
    for order in orders:
        matching_items = [item for item in order.items if item.product_id in product_ids]
        if not matching_items:
            continue
        total_orders += 1
        if order.vendor_status in {"confirmed", "processing", "ready", "delivered"}:
            total_sales += sum(item.line_total for item in matching_items)

    return stores_count, len(products), total_orders, round(total_sales, 2)


def _serialize_vendor(db: Session, user: User) -> AdminVendorRead:
    application = _vendor_application_by_user(db, user.id)
    stores_count, products_count, total_orders, total_sales = _vendor_activity_summary(db, user.id)
    return AdminVendorRead(
        id=str(user.id),
        user_id=user.id,
        business_name=application.business_name if application else user.full_name,
        business_category=application.business_category if application else "General",
        status="suspended" if user.vendor_status == VendorStatus.SUSPENDED else "active",
        email=user.email,
        phone_number=user.phone_number,
        total_stores=stores_count,
        total_products=products_count,
        total_orders=total_orders,
        total_sales=total_sales,
        joined_at=user.created_at,
    )


def _build_discount(price: int, old_price: int | None) -> str | None:
    if old_price is None or old_price <= 0 or old_price <= price:
        return None

    percentage = round(((old_price - price) / old_price) * 100)
    return f"{percentage}% off"


def _taxonomy_lookup_by_slug() -> dict[str, dict]:
    return {entry["slug"]: entry for entry in ODOS_CATEGORY_TAXONOMY}


def _normalize_slug(value: str) -> str:
    return _slugify(value)


def _normalize_string_list(values: list[str] | None) -> list[str] | None:
    return _normalize_list(values)


def _resolve_product_taxonomy(
    *,
    category: str,
    subcategory: str | None,
    category_slugs: list[str] | None,
    subcategory_slugs: list[str] | None,
) -> tuple[str, str | None, list[str] | None, list[str] | None]:
    normalized_category_slugs = _normalize_string_list(
        category_slugs or [_normalize_slug(category)]
    )
    normalized_subcategory_slugs = _normalize_string_list(
        subcategory_slugs or ([_normalize_slug(subcategory)] if subcategory else None)
    )
    primary_category = category.strip()
    primary_subcategory = subcategory.strip() if subcategory else None

    taxonomy_lookup = _taxonomy_lookup_by_slug()
    if normalized_category_slugs:
        primary_entry = taxonomy_lookup.get(normalized_category_slugs[0])
        if primary_entry:
            primary_category = primary_entry["title"]

    if normalized_subcategory_slugs and normalized_category_slugs:
        for category_slug in normalized_category_slugs:
            entry = taxonomy_lookup.get(category_slug)
            if not entry:
                continue
            slug_to_title = {
                _normalize_slug(item): item for item in entry.get("subcategories", [])
            }
            for sub_slug in normalized_subcategory_slugs:
                if sub_slug in slug_to_title:
                    primary_subcategory = slug_to_title[sub_slug]
                    return (
                        primary_category,
                        primary_subcategory,
                        normalized_category_slugs,
                        normalized_subcategory_slugs,
                    )

    return (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    )


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


def _sync_platform_store_avatar(store: Store, avatar_url: str | None) -> None:
    if avatar_url:
        store.image_url = avatar_url
        store.image_banner_url = avatar_url


def login_admin_user(db: Session, credentials: UserLogin) -> AuthToken:
    session = login_user(db, credentials)
    if session.user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account does not have admin access.",
        )
    return session


def get_admin_bootstrap_status(db: Session) -> AdminBootstrapStatusRead:
    admin_count = db.scalar(
        select(func.count(User.id)).where(User.role == UserRole.ADMIN)
    ) or 0
    return AdminBootstrapStatusRead(bootstrap_enabled=admin_count == 0)


def bootstrap_first_admin(db: Session, payload: UserCreate) -> AuthToken:
    if not get_admin_bootstrap_status(db).bootstrap_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin bootstrap is no longer available.",
        )

    normalized_email = payload.email.lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    if payload.phone_number:
        existing_phone = db.scalar(
            select(User).where(User.phone_number == payload.phone_number)
        )
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this phone number already exists.",
            )

    user = User(
        full_name=payload.full_name,
        email=normalized_email,
        phone_number=payload.phone_number,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with these details already exists.",
        ) from None

    return build_auth_token(db, user)


def get_admin_me(current_user: User) -> User:
    require_admin(current_user)
    return current_user


async def update_admin_profile(
    db: Session,
    current_user: User,
    *,
    full_name: str | None,
    phone_number: str | None,
    avatar_image: UploadFile | None,
) -> User:
    require_admin(current_user)

    if full_name is not None:
        cleaned_name = full_name.strip()
        if len(cleaned_name) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Full name must be at least 2 characters long.",
            )
        current_user.full_name = cleaned_name

    if phone_number is not None:
        cleaned_phone = phone_number.strip() or None
        if cleaned_phone:
            existing_phone = db.scalar(
                select(User).where(
                    User.phone_number == cleaned_phone,
                    User.id != current_user.id,
                )
            )
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this phone number already exists.",
                )
        current_user.phone_number = cleaned_phone

    if avatar_image is not None:
        previous_avatar_url = current_user.avatar_url
        current_user.avatar_url = await save_image_upload(avatar_image, folder="users/avatars")
        if previous_avatar_url and previous_avatar_url != current_user.avatar_url:
            remove_media_file(previous_avatar_url)

    if current_user.avatar_url:
        platform_store = _ensure_platform_store(db)
        _sync_platform_store_avatar(platform_store, current_user.avatar_url)

    try:
        db.commit()
        db.refresh(current_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't save those admin profile changes.",
        ) from None

    return current_user


def get_admin_dashboard(db: Session, current_user: User) -> AdminDashboardRead:
    require_admin(current_user)

    stats = AdminDashboardStatsRead(
        total_users=db.scalar(select(func.count(User.id))) or 0,
        total_vendors=db.scalar(
            select(func.count(User.id)).where(
                User.vendor_status.in_([VendorStatus.APPROVED, VendorStatus.SUSPENDED])
            )
        )
        or 0,
        pending_vendor_applications=db.scalar(
            select(func.count(VendorApplication.id)).where(
                VendorApplication.status.in_([VendorStatus.PENDING, VendorStatus.UNDER_REVIEW])
            )
        )
        or 0,
        total_stores=db.scalar(select(func.count(Store.id))) or 0,
        total_products=db.scalar(select(func.count(Product.id))) or 0,
        total_orders=db.scalar(select(func.count(Order.id))) or 0,
        pending_orders=db.scalar(
            select(func.count(Order.id)).where(
                Order.vendor_status.in_(["pending", "confirmed", "processing", "ready"])
            )
        )
        or 0,
        total_revenue=round(
            float(db.scalar(select(func.coalesce(func.sum(Order.total_amount), 0.0))) or 0.0),
            2,
        ),
    )

    recent_orders = list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
            .limit(5)
        ).all()
    )
    recent_notifications = list(
        db.scalars(
            select(NotificationEvent)
            .order_by(NotificationEvent.created_at.desc())
            .limit(5)
        ).all()
    )
    read_keys = set(
        db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == current_user.id)
        ).all()
    )

    vendor_applications_page = list_vendor_applications(db, current_user, limit=5)

    return AdminDashboardRead(
        stats=stats,
        recent_orders=[_serialize_order(db, order) for order in recent_orders],
        recent_vendor_applications=[
            item.model_dump() for item in vendor_applications_page.items
        ],
        recent_notifications=[
            _serialize_notification(notification, is_read=str(notification.id) in read_keys)
            for notification in recent_notifications
        ],
    )


def list_admin_users(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminUserRead]:
    require_admin(current_user)
    statement = select(User).order_by(User.created_at.desc())
    users, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_user(user) for user in users],
        has_more=has_more,
    )


def get_admin_user(db: Session, current_user: User, user_id: str) -> AdminUserDetailRead:
    require_admin(current_user)
    user = db.scalar(
        select(User)
        .options(
            selectinload(User.auth_accounts),
            selectinload(User.vendor_application),
            selectinload(User.saved_addresses),
            selectinload(User.saved_payment_methods),
            selectinload(User.orders),
            selectinload(User.reviews),
            selectinload(User.return_requests).selectinload(ReturnRequest.order_item),
            selectinload(User.return_requests).selectinload(ReturnRequest.order),
            selectinload(User.payment_transactions).selectinload(PaymentTransaction.order),
            selectinload(User.payment_transactions).selectinload(PaymentTransaction.user),
            selectinload(User.cart_items),
            selectinload(User.wishlist_items),
            selectinload(User.notification_events),
            selectinload(User.customer_wallet).selectinload(CustomerWallet.transactions),
        )
        .where(User.id == user_id)
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _serialize_user_detail(db, user)


def update_admin_user_status(
    db: Session,
    current_user: User,
    user_id: str,
    payload: AdminUserStatusUpdate,
) -> AdminUserRead:
    require_admin(current_user)
    if payload.account_status not in SUPPORTED_ACCOUNT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported account status.")

    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = payload.account_status == "active"
    db.commit()
    db.refresh(user)
    return _serialize_user(user)


def list_admin_vendors(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminVendorRead]:
    require_admin(current_user)
    statement = (
        select(User)
        .where(User.vendor_status.in_([VendorStatus.APPROVED, VendorStatus.SUSPENDED]))
        .order_by(User.created_at.desc())
    )
    vendors, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_vendor(db, vendor) for vendor in vendors],
        has_more=has_more,
    )


def get_admin_vendor(db: Session, current_user: User, vendor_id: str) -> AdminVendorRead:
    require_admin(current_user)
    vendor = db.scalar(select(User).where(User.id == vendor_id))
    if not vendor or vendor.vendor_status not in {VendorStatus.APPROVED, VendorStatus.SUSPENDED}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found.")
    return _serialize_vendor(db, vendor)


def update_admin_vendor_status(
    db: Session,
    current_user: User,
    vendor_id: str,
    payload: AdminVendorStatusUpdate,
) -> AdminVendorRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_VENDOR_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported vendor status.")

    vendor = db.scalar(select(User).where(User.id == vendor_id))
    if not vendor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found.")

    if payload.status == "suspended":
        vendor.vendor_status = VendorStatus.SUSPENDED
        for store in db.scalars(select(Store).where(Store.vendor_user_id == vendor.id)).all():
            store.status = "suspended"
            store.is_active = False
    else:
        vendor.vendor_status = VendorStatus.APPROVED
        if vendor.role != UserRole.ADMIN:
            vendor.role = UserRole.VENDOR
        for store in db.scalars(select(Store).where(Store.vendor_user_id == vendor.id)).all():
            if store.status == "suspended":
                store.status = "active"
                store.is_active = True

    db.commit()
    db.refresh(vendor)
    return _serialize_vendor(db, vendor)


def list_admin_stores(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminStoreRead]:
    require_admin(current_user)
    statement = select(Store).order_by(Store.created_at.desc())
    stores, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_store(store) for store in stores],
        has_more=has_more,
    )


def get_admin_store(db: Session, current_user: User, store_id: str) -> AdminStoreDetailRead:
    require_admin(current_user)
    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
    vendor = db.scalar(select(User).where(User.id == store.vendor_user_id)) if store.vendor_user_id else None
    market = db.scalar(select(Market).where(Market.id == store.market_id)) if store.market_id else None
    products = list(
        db.scalars(select(Product).where(Product.store_id == store.id).order_by(Product.created_at.desc())).all()
    )
    return _serialize_store_detail(db, store, vendor=vendor, market=market, products=products)


def update_admin_store_status(
    db: Session,
    current_user: User,
    store_id: str,
    payload: AdminStoreStatusUpdate,
) -> AdminStoreRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_STORE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported store status.")

    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")

    store.status = payload.status
    store.is_active = payload.status == "active"
    db.commit()
    db.refresh(store)
    broadcast_catalog_store_change(store)
    return _serialize_store(store)


async def create_admin_store(
    db: Session,
    current_user: User,
    payload: AdminStoreUpsert,
    logo_image: UploadFile | None,
    banner_image: UploadFile | None,
) -> AdminStoreRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_STORE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported store status.")

    market = None
    if payload.market_id:
        market = db.scalar(select(Market).where(Market.id == payload.market_id))
        if not market:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")

    category_slug = _normalize_slug(payload.category)
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category_slug)
    store = Store(
        id=_generate_store_id(),
        slug=payload.slug or _slugify(payload.name) or _generate_store_id(),
        title=payload.name,
        category=taxonomy_entry["title"] if taxonomy_entry else payload.category,
        audience_slugs=_normalize_list(payload.audience_slugs),
        market_id=market.id if market else None,
        market_slug=market.slug if market else None,
        image_key=taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.category),
        image_url=await save_image_upload(logo_image, folder="stores/logo") if logo_image else None,
        image_banner_key=taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.category),
        image_banner_url=await save_image_upload(banner_image, folder="stores/banner") if banner_image else None,
        rating=4.6,
        address=payload.location,
        phone=None,
        email="support@odos.app",
        city=payload.city,
        region=payload.region,
        distance_km=None,
        travel_minutes=None,
        description=payload.description or payload.name,
        status=payload.status,
        vendor_user_id=None,
        sort_order=(db.scalar(select(func.coalesce(func.max(Store.sort_order), 0))) or 0) + 1,
        is_active=payload.status == "active",
    )
    db.add(store)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A store with that name already exists.",
        ) from exc
    db.refresh(store)
    broadcast_catalog_store_change(store)
    return _serialize_store(store)


def list_admin_markets(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminMarketRead]:
    require_admin(current_user)
    statement = select(Market).order_by(Market.sort_order.asc(), Market.title.asc())
    markets, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_market(market) for market in markets],
        has_more=has_more,
    )


async def create_admin_market(
    db: Session,
    current_user: User,
    payload: AdminMarketUpsert,
    image_file: UploadFile | None = None,
) -> AdminMarketRead:
    require_admin(current_user)
    market = Market(
        id=f"market-{uuid.uuid4().hex[:8]}",
        slug=payload.slug or _slugify(payload.name) or f"market-{uuid.uuid4().hex[:6]}",
        title=payload.name,
        image_key=payload.image or "market",
        sort_order=(db.scalar(select(func.coalesce(func.max(Market.sort_order), 0))) or 0) + 1,
        is_active=payload.status != "disabled",
    )
    if image_file:
        market.image_url = await save_image_upload(image_file, folder="markets")
    db.add(market)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A market with that name already exists.",
        ) from exc
    db.refresh(market)
    broadcast_catalog_market_change(market)
    return _serialize_market(market)


async def update_admin_market(
    db: Session,
    current_user: User,
    market_id: str,
    payload: AdminMarketUpsert,
    image_file: UploadFile | None = None,
) -> AdminMarketRead:
    require_admin(current_user)
    market = db.scalar(select(Market).where(Market.id == market_id))
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")

    market.title = payload.name
    market.slug = payload.slug or _slugify(payload.name) or market.slug
    market.image_key = payload.image or market.image_key
    if image_file:
        if market.image_url:
            remove_media_file(market.image_url)
        market.image_url = await save_image_upload(image_file, folder="markets")
    market.is_active = payload.status != "disabled"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A market with that name already exists.",
        ) from exc
    db.refresh(market)
    broadcast_catalog_market_change(market)
    return _serialize_market(market)


def delete_admin_market(db: Session, current_user: User, market_id: str) -> None:
    require_admin(current_user)
    market = db.scalar(select(Market).where(Market.id == market_id))
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")
    market.is_active = False
    db.commit()
    broadcast_catalog_market_change(market)


def _validate_promo_banner_payload(payload: AdminPromoBannerUpsert) -> None:
    link_type = normalize_promo_link_type(payload.link_type)
    placement = normalize_promo_placement(payload.placement)
    target = (payload.cta_link or "").strip()
    campaign_tag = (payload.campaign_tag or "").strip()

    if link_type in {"category", "product", "store", "external", "screen"} and not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a destination target for this banner tap action.",
        )
    if link_type == "campaign" and not campaign_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a campaign for this banner.",
        )
    if link_type == "external" and target and not (
        target.startswith("http://") or target.startswith("https://")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="External links must start with http:// or https://",
        )
    if campaign_tag and campaign_tag not in dict(PROMO_CAMPAIGN_TAGS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported campaign tag.",
        )
    if placement not in {"home", "deals"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported banner placement.",
        )


def _serialize_promo_banner(banner: PromoBanner) -> AdminPromoBannerRead:
    link_type = normalize_promo_link_type(banner.link_type)
    placement = normalize_promo_placement(banner.placement)
    return AdminPromoBannerRead(
        id=banner.id,
        title=banner.title,
        subtitle=banner.subtitle,
        cta_label=banner.cta_label,
        cta_link=banner.cta_link,
        image_url=banner.image_url,
        accent=banner.accent,
        sort_order=banner.sort_order,
        status="active" if banner.is_active else "disabled",
        link_type=link_type,
        campaign_tag=banner.campaign_tag,
        placement=placement,
        destination_label=describe_promo_destination(
            link_type=link_type,
            cta_link=banner.cta_link,
            campaign_tag=banner.campaign_tag,
        ),
        starts_at=banner.starts_at,
        ends_at=banner.ends_at,
        created_at=banner.created_at,
        updated_at=banner.updated_at,
    )


def broadcast_catalog_promo_banner_change(banner: PromoBanner) -> None:
    from app.core.cache import invalidate_catalog_promo_banners

    invalidate_catalog_promo_banners()
    realtime_manager.broadcast_event_sync(
        "catalog.promo_banner.changed",
        {
            "banner_id": str(banner.id),
            "status": "active" if banner.is_active else "disabled",
            "is_active": banner.is_active,
        },
    )


def list_admin_promo_banners(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminPromoBannerRead]:
    require_admin(current_user)
    statement = select(PromoBanner).order_by(
        PromoBanner.sort_order.asc(),
        PromoBanner.created_at.desc(),
    )
    banners, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_promo_banner(banner) for banner in banners],
        has_more=has_more,
    )


async def create_admin_promo_banner(
    db: Session,
    current_user: User,
    payload: AdminPromoBannerUpsert,
    image_file: UploadFile | None = None,
) -> AdminPromoBannerRead:
    require_admin(current_user)
    _validate_promo_banner_payload(payload)
    if payload.starts_at and payload.ends_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    next_sort_order = payload.sort_order
    if next_sort_order is None:
        next_sort_order = (db.scalar(select(func.coalesce(func.max(PromoBanner.sort_order), 0))) or 0) + 1

    banner = PromoBanner(
        title=payload.title,
        subtitle=payload.subtitle,
        cta_label=payload.cta_label or "Shop now",
        cta_link=payload.cta_link,
        accent=payload.accent,
        sort_order=next_sort_order,
        is_active=payload.status != "disabled",
        link_type=normalize_promo_link_type(payload.link_type),
        campaign_tag=payload.campaign_tag,
        placement=normalize_promo_placement(payload.placement),
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    if image_file:
        banner.image_url = await save_image_upload(image_file, folder="promo-banners")

    db.add(banner)
    db.commit()
    db.refresh(banner)
    broadcast_catalog_promo_banner_change(banner)
    return _serialize_promo_banner(banner)


async def update_admin_promo_banner(
    db: Session,
    current_user: User,
    banner_id: str,
    payload: AdminPromoBannerUpsert,
    image_file: UploadFile | None = None,
) -> AdminPromoBannerRead:
    require_admin(current_user)
    _validate_promo_banner_payload(payload)
    if payload.starts_at and payload.ends_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    banner.title = payload.title
    banner.subtitle = payload.subtitle
    banner.cta_label = payload.cta_label or "Shop now"
    banner.cta_link = payload.cta_link
    banner.accent = payload.accent
    banner.link_type = normalize_promo_link_type(payload.link_type)
    banner.campaign_tag = payload.campaign_tag
    banner.placement = normalize_promo_placement(payload.placement)
    if payload.sort_order is not None:
        banner.sort_order = payload.sort_order
    banner.is_active = payload.status != "disabled"
    banner.starts_at = payload.starts_at
    banner.ends_at = payload.ends_at

    if image_file:
        if banner.image_url:
            remove_media_file(banner.image_url)
        banner.image_url = await save_image_upload(image_file, folder="promo-banners")

    db.commit()
    db.refresh(banner)
    broadcast_catalog_promo_banner_change(banner)
    return _serialize_promo_banner(banner)


def get_admin_promo_banner(db: Session, current_user: User, banner_id: str) -> AdminPromoBannerRead:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    return _serialize_promo_banner(banner)


def archive_admin_promo_banner(db: Session, current_user: User, banner_id: str) -> None:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    banner.is_active = False
    db.commit()
    broadcast_catalog_promo_banner_change(banner)


def _normalize_flash_event_slug(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)


def _serialize_flash_sale_event(
    db: Session,
    event: FlashSaleEvent,
) -> AdminFlashSaleEventRead:
    product_ids = list(
        db.scalars(
            select(FlashSaleEventProduct.product_id)
            .where(FlashSaleEventProduct.event_id == event.id)
            .order_by(FlashSaleEventProduct.sort_order.asc(), FlashSaleEventProduct.product_id.asc())
        ).all()
    )
    return AdminFlashSaleEventRead(
        id=event.id,
        slug=event.slug,
        title=event.title,
        subtitle=event.subtitle,
        image_url=event.image_url,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        sort_order=event.sort_order,
        status="active" if event.is_active else "disabled",
        product_ids=product_ids,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def broadcast_catalog_flash_sale_event_change(event: FlashSaleEvent) -> None:
    from app.core.cache import invalidate_catalog_flash_sale_events

    invalidate_catalog_flash_sale_events()
    realtime_manager.broadcast_event_sync(
        "catalog.flash_sale_event.changed",
        {
            "event_id": str(event.id),
            "slug": event.slug,
            "status": "active" if event.is_active else "disabled",
            "is_active": event.is_active,
        },
    )


def _replace_flash_sale_event_products(
    db: Session,
    event: FlashSaleEvent,
    product_ids: list[str],
) -> None:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for product_id in product_ids:
        cleaned = product_id.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized_ids.append(cleaned)

    if normalized_ids:
        existing_count = db.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.id.in_(normalized_ids),
                Product.is_active.is_(True),
            )
        )
        if existing_count != len(normalized_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more selected products could not be found.",
            )

    db.execute(
        FlashSaleEventProduct.__table__.delete().where(
            FlashSaleEventProduct.event_id == event.id
        )
    )
    for index, product_id in enumerate(normalized_ids):
        db.add(
            FlashSaleEventProduct(
                event_id=event.id,
                product_id=product_id,
                sort_order=index + 1,
            )
        )


def list_admin_flash_sale_events(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminFlashSaleEventRead]:
    require_admin(current_user)
    statement = select(FlashSaleEvent).order_by(
        FlashSaleEvent.sort_order.asc(),
        FlashSaleEvent.ends_at.desc(),
    )
    events, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_flash_sale_event(db, event) for event in events],
        has_more=has_more,
    )


def create_admin_flash_sale_event(
    db: Session,
    current_user: User,
    payload: AdminFlashSaleEventUpsert,
) -> AdminFlashSaleEventRead:
    require_admin(current_user)
    slug = _normalize_flash_event_slug(payload.slug)
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event slug is required.")

    if payload.starts_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    existing = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.slug == slug))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A flash sale event with this slug already exists.",
        )

    next_sort_order = payload.sort_order
    if next_sort_order is None:
        next_sort_order = (db.scalar(select(func.coalesce(func.max(FlashSaleEvent.sort_order), 0))) or 0) + 1

    event = FlashSaleEvent(
        slug=slug,
        title=payload.title,
        subtitle=payload.subtitle,
        sort_order=next_sort_order,
        is_active=payload.status != "disabled",
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    db.add(event)
    db.flush()
    _replace_flash_sale_event_products(db, event, payload.product_ids)
    db.commit()
    db.refresh(event)
    broadcast_catalog_flash_sale_event_change(event)
    return _serialize_flash_sale_event(db, event)


def update_admin_flash_sale_event(
    db: Session,
    current_user: User,
    event_id: str,
    payload: AdminFlashSaleEventUpsert,
) -> AdminFlashSaleEventRead:
    require_admin(current_user)
    slug = _normalize_flash_event_slug(payload.slug)
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event slug is required.")

    if payload.starts_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    try:
        normalized_id = uuid.UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.") from exc

    event = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.id == normalized_id))
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.")

    conflict = db.scalar(
        select(FlashSaleEvent).where(
            FlashSaleEvent.slug == slug,
            FlashSaleEvent.id != normalized_id,
        )
    )
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A flash sale event with this slug already exists.",
        )

    event.slug = slug
    event.title = payload.title
    event.subtitle = payload.subtitle
    if payload.sort_order is not None:
        event.sort_order = payload.sort_order
    event.is_active = payload.status != "disabled"
    event.starts_at = payload.starts_at
    event.ends_at = payload.ends_at
    _replace_flash_sale_event_products(db, event, payload.product_ids)
    db.commit()
    db.refresh(event)
    broadcast_catalog_flash_sale_event_change(event)
    return _serialize_flash_sale_event(db, event)


def archive_admin_flash_sale_event(db: Session, current_user: User, event_id: str) -> None:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.") from exc

    event = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.id == normalized_id))
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.")

    event.is_active = False
    db.commit()
    broadcast_catalog_flash_sale_event_change(event)


def list_admin_categories(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminCategoryRead]:
    require_admin(current_user)
    statement = select(Category).order_by(Category.sort_order.asc(), Category.title.asc())
    categories, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_category(category) for category in categories],
        has_more=has_more,
    )


async def create_admin_category(
    db: Session,
    current_user: User,
    payload: AdminCategoryUpsert,
    image_file: UploadFile | None,
) -> AdminCategoryRead:
    require_admin(current_user)
    category_slug = payload.slug or _slugify(payload.name) or f"category-{uuid.uuid4().hex[:6]}"
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category_slug)
    category = Category(
        id=f"category-{uuid.uuid4().hex[:8]}",
        slug=category_slug,
        title=payload.name,
        subtitle=payload.description or payload.name,
        image_key=payload.image or (taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.name)),
        image_url=await save_image_upload(image_file, folder="categories") if image_file else None,
        subcategories=_normalize_list(payload.subcategories),
        sort_order=(db.scalar(select(func.coalesce(func.max(Category.sort_order), 0))) or 0) + 1,
        is_active=payload.status != "disabled",
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with that name already exists.",
        ) from exc
    db.refresh(category)
    broadcast_catalog_category_change(category)
    return _serialize_category(category)


async def update_admin_category(
    db: Session,
    current_user: User,
    category_id: str,
    payload: AdminCategoryUpsert,
    image_file: UploadFile | None,
) -> AdminCategoryRead:
    require_admin(current_user)
    category = db.scalar(select(Category).where(Category.id == category_id))
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")

    category.title = payload.name
    category.slug = payload.slug or _slugify(payload.name) or category.slug
    category.subtitle = payload.description or payload.name
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category.slug)
    category.image_key = payload.image or category.image_key or (
        taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.name)
    )
    category.subcategories = _normalize_list(payload.subcategories)
    if image_file is not None:
        if category.image_url and category.image_url != category.image_key:
            remove_media_file(category.image_url)
        category.image_url = await save_image_upload(image_file, folder="categories")
    category.is_active = payload.status != "disabled"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with that name already exists.",
        ) from exc
    db.refresh(category)
    broadcast_catalog_category_change(category)
    return _serialize_category(category)


def delete_admin_category(
    db: Session,
    current_user: User,
    category_id: str,
    *,
    permanent: bool = False,
) -> None:
    require_admin(current_user)
    category = db.scalar(select(Category).where(Category.id == category_id))
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    if permanent:
        if category.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Disable this category before deleting it permanently.",
            )
        if category.image_url:
            remove_media_file(category.image_url)
        deleted_snapshot = Category(
            id=category.id,
            slug=category.slug,
            title=category.title,
            subtitle=category.subtitle,
            image_key=category.image_key,
            image_url=category.image_url,
            subcategories=category.subcategories,
            sort_order=category.sort_order,
            is_active=False,
        )
        db.delete(category)
        db.commit()
        broadcast_catalog_category_change(deleted_snapshot)
        return
    category.is_active = False
    db.commit()
    broadcast_catalog_category_change(category)


def _serialize_admin_products(db: Session, products: list[Product]) -> list[AdminProductRead]:
    store_ids = {product.store_id for product in products if product.store_id}
    vendor_ids = {product.vendor_user_id for product in products if product.vendor_user_id}
    store_lookup = {
        store.id: store
        for store in db.scalars(select(Store).where(Store.id.in_(store_ids))).all()
    } if store_ids else {}
    vendor_lookup = {
        vendor.id: vendor
        for vendor in db.scalars(select(User).where(User.id.in_(vendor_ids))).all()
    } if vendor_ids else {}
    return [
        _serialize_product(
            product,
            store=store_lookup.get(product.store_id),
            vendor=vendor_lookup.get(product.vendor_user_id) if product.vendor_user_id else None,
        )
        for product in products
    ]


def list_admin_products(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminProductRead]:
    require_admin(current_user)
    statement = select(Product).order_by(
        case((Product.status == "pending", 0), else_=1),
        Product.updated_at.desc(),
        Product.created_at.desc(),
    )
    products, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=_serialize_admin_products(db, products),
        has_more=has_more,
    )


def get_admin_product(db: Session, current_user: User, product_id: str) -> AdminProductRead:
    require_admin(current_user)
    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    store = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )


def _get_store_for_admin_product(db: Session, store_id: str | None) -> Store:
    if store_id:
        store = db.scalar(select(Store).where(Store.id == store_id))
        if not store:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
        return store

    return _ensure_platform_store(db)


async def create_admin_product(
    db: Session,
    current_user: User,
    payload: AdminProductCreate,
    images: list[UploadFile] | None,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    store = _get_store_for_admin_product(db, payload.store_id)

    image_urls = await save_image_uploads(images, folder="products")
    image_url = image_urls[0] if image_urls else None
    (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    ) = _resolve_product_taxonomy(
        category=payload.category,
        subcategory=payload.subcategory,
        category_slugs=payload.category_slugs,
        subcategory_slugs=payload.subcategory_slugs,
    )
    product = Product(
        id=_generate_product_id(),
        audience_slug=payload.audience_slug or ((store.audience_slugs or [None])[0] if store else None),
        section=payload.section,
        title=payload.name,
        category=primary_category,
        subcategory=primary_subcategory,
        category_slugs=normalized_category_slugs,
        subcategory_slugs=normalized_subcategory_slugs,
        price=payload.price,
        old_price=payload.old_price,
        discount=_build_discount(payload.price, payload.old_price),
        rating=payload.rating,
        reviews=payload.reviews,
        image_key=payload.image_key or _infer_image_key(primary_category),
        image_url=image_url,
        image_urls=image_urls or None,
        color_options=_normalize_list(payload.color_options),
        size_options=_normalize_list(payload.size_options),
        specifications=_normalize_list(payload.specifications),
        placement_tags=_normalize_list(payload.placement_tags),
        description=payload.description,
        stock=payload.stock,
        status=payload.status,
        store_id=store.id,
        vendor_user_id=store.vendor_user_id,
        sort_order=0,
        is_active=payload.status == "active",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(product, store=store, vendor=vendor)


async def update_admin_product(
    db: Session,
    current_user: User,
    product_id: str,
    payload: AdminProductCreate,
    images: list[UploadFile] | None,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    store = _get_store_for_admin_product(db, payload.store_id)
    uploaded_image_urls = await save_image_uploads(images, folder="products")
    existing_image_urls = list(product.image_urls or ([] if not product.image_url else [product.image_url]))
    next_image_urls = existing_image_urls + uploaded_image_urls if uploaded_image_urls else existing_image_urls

    (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    ) = _resolve_product_taxonomy(
        category=payload.category,
        subcategory=payload.subcategory,
        category_slugs=payload.category_slugs,
        subcategory_slugs=payload.subcategory_slugs,
    )

    product.audience_slug = payload.audience_slug or ((store.audience_slugs or [None])[0] if store else None)
    product.section = payload.section
    product.title = payload.name
    product.category = primary_category
    product.subcategory = primary_subcategory
    product.category_slugs = normalized_category_slugs
    product.subcategory_slugs = normalized_subcategory_slugs
    product.price = payload.price
    product.old_price = payload.old_price
    product.discount = _build_discount(payload.price, payload.old_price)
    product.rating = payload.rating
    product.reviews = payload.reviews
    product.image_key = payload.image_key or product.image_key or _infer_image_key(primary_category)
    product.image_urls = next_image_urls or None
    product.image_url = next_image_urls[0] if next_image_urls else None
    product.color_options = _normalize_list(payload.color_options)
    product.size_options = _normalize_list(payload.size_options)
    product.specifications = _normalize_list(payload.specifications)
    product.placement_tags = _normalize_list(payload.placement_tags)
    product.description = payload.description
    product.stock = payload.stock
    product.status = payload.status
    product.store_id = store.id
    product.vendor_user_id = store.vendor_user_id
    product.is_active = payload.status == "active"

    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )


def update_admin_product_status(
    db: Session,
    current_user: User,
    product_id: str,
    payload: AdminProductStatusUpdate,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    product.status = payload.status
    product.is_active = payload.status == "active"
    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    store = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )


def _serialize_admin_vouchers(db: Session, vouchers: list[Voucher]) -> list[AdminVoucherRead]:
    stats_map = _voucher_stats_map(db, [voucher.id for voucher in vouchers])
    store_name_map = {
        store_id: title
        for store_id, title in db.execute(
            select(Store.id, Store.title).where(
                Store.id.in_([voucher.store_id for voucher in vouchers if voucher.store_id])
            )
        ).all()
    }
    return [
        _serialize_voucher(
            voucher,
            store_name=store_name_map.get(voucher.store_id or ""),
            redemption_count=int(stats_map.get(voucher.id, {}).get("redemption_count", 0)),
            unique_user_count=int(stats_map.get(voucher.id, {}).get("unique_user_count", 0)),
            total_discount_amount=float(
                stats_map.get(voucher.id, {}).get("total_discount_amount", 0)
            ),
        )
        for voucher in vouchers
    ]


def list_admin_vouchers(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminVoucherRead]:
    require_admin(current_user)
    statement = select(Voucher).order_by(Voucher.created_at.desc(), Voucher.title.asc())
    vouchers, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=_serialize_admin_vouchers(db, vouchers),
        has_more=has_more,
    )


def _get_admin_voucher(db: Session, voucher_id: str) -> Voucher:
    try:
        normalized_id = uuid.UUID(str(voucher_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voucher not found.",
        ) from exc

    voucher = db.get(Voucher, normalized_id)
    if not voucher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found.")
    return voucher


def create_admin_voucher(
    db: Session,
    current_user: User,
    payload: AdminVoucherUpsert,
) -> AdminVoucherRead:
    require_admin(current_user)
    _validate_voucher_payload(payload)

    target_store = None
    if payload.scope == "store":
        target_store = db.scalar(select(Store).where(Store.id == payload.store_id))
        if not target_store:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected store was not found.",
            )

    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)
    voucher = Voucher(
        code=payload.code,
        title=payload.title,
        description=payload.description,
        issuer_name=payload.issuer_name or (target_store.title if target_store else None),
        scope=payload.scope,
        availability=payload.availability,
        store_id=target_store.id if target_store else None,
        reward_text=build_voucher_reward_text(payload.discount_type, discount_value),
        discount_type=payload.discount_type,
        discount_value=discount_value,
        min_subtotal=round(payload.min_subtotal, 2),
        max_discount=round(payload.max_discount, 2) if payload.max_discount is not None else None,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        is_active=payload.is_active,
        approval_status="approved",
        reviewed_by_user_id=current_user.id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    db.add(voucher)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That voucher code already exists.",
        ) from exc

    db.refresh(voucher)
    return _serialize_voucher(voucher, store_name=target_store.title if target_store else None)


def update_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
    payload: AdminVoucherUpsert,
) -> AdminVoucherRead:
    require_admin(current_user)
    _validate_voucher_payload(payload)

    voucher = _get_admin_voucher(db, voucher_id)
    target_store = None
    if payload.scope == "store":
        target_store = db.scalar(select(Store).where(Store.id == payload.store_id))
        if not target_store:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected store was not found.",
            )
    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)

    voucher.code = payload.code
    voucher.title = payload.title
    voucher.description = payload.description
    voucher.issuer_name = payload.issuer_name or (target_store.title if target_store else None)
    voucher.scope = payload.scope
    voucher.availability = payload.availability
    voucher.store_id = target_store.id if target_store else None
    voucher.reward_text = build_voucher_reward_text(payload.discount_type, discount_value)
    voucher.discount_type = payload.discount_type
    voucher.discount_value = discount_value
    voucher.min_subtotal = round(payload.min_subtotal, 2)
    voucher.max_discount = round(payload.max_discount, 2) if payload.max_discount is not None else None
    voucher.usage_limit = payload.usage_limit
    voucher.per_user_limit = payload.per_user_limit
    voucher.is_active = payload.is_active
    voucher.starts_at = payload.starts_at
    voucher.ends_at = payload.ends_at

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That voucher code already exists.",
        ) from exc

    db.refresh(voucher)
    stats_map = _voucher_stats_map(db, [voucher.id])
    voucher_stats = stats_map.get(voucher.id, {})
    return _serialize_voucher(
        voucher,
        store_name=target_store.title if target_store else None,
        redemption_count=int(voucher_stats.get("redemption_count", 0)),
        unique_user_count=int(voucher_stats.get("unique_user_count", 0)),
        total_discount_amount=float(voucher_stats.get("total_discount_amount", 0)),
    )


def archive_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
) -> None:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)
    voucher.is_active = False
    db.commit()


def review_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
    payload: AdminVoucherReview,
) -> AdminVoucherRead:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)

    if payload.approval_status not in {"approved", "rejected", "disabled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval status must be approved, rejected, or disabled.",
        )

    voucher.approval_status = payload.approval_status
    voucher.reviewed_by_user_id = current_user.id
    voucher.review_notes = payload.review_notes
    if payload.approval_status == "approved":
        voucher.is_active = payload.is_active if payload.is_active is not None else True
    elif payload.approval_status in {"rejected", "disabled"}:
        voucher.is_active = False

    db.commit()
    db.refresh(voucher)

    store_name = None
    if voucher.store_id:
        store_name = db.scalar(select(Store.title).where(Store.id == voucher.store_id))
    stats_map = _voucher_stats_map(db, [voucher.id])
    voucher_stats = stats_map.get(voucher.id, {})
    return _serialize_voucher(
        voucher,
        store_name=store_name,
        redemption_count=int(voucher_stats.get("redemption_count", 0)),
        unique_user_count=int(voucher_stats.get("unique_user_count", 0)),
        total_discount_amount=float(voucher_stats.get("total_discount_amount", 0)),
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

    store_name_map = {
        store_id: title
        for store_id, title in db.execute(
            select(Store.id, Store.title).where(Store.id.in_(store_ids))
        ).all()
    }
    return products, store_name_map


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
        is_hidden=review.is_hidden,
        moderation_reason=review.moderation_reason,
        moderated_at=review.moderated_at,
        created_at=review.created_at,
        updated_at=review.updated_at,
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


def _get_admin_review(db: Session, review_id: str) -> Review:
    try:
        normalized_id = uuid.UUID(str(review_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found.",
        ) from exc

    review = db.scalar(
        select(Review)
        .options(
            selectinload(Review.user),
            selectinload(Review.order).selectinload(Order.items),
        )
        .where(Review.id == normalized_id)
    )
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found.")
    return review


def list_admin_reviews(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminReviewRead]:
    require_admin(current_user)
    statement = (
        select(Review)
        .options(
            selectinload(Review.user),
            selectinload(Review.order).selectinload(Order.items),
        )
        .order_by(Review.updated_at.desc(), Review.created_at.desc())
    )
    reviews, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    products, store_name_map = _resolve_review_context(db, reviews)
    return AdminPageRead(
        items=[
            _build_admin_review_read(
                review,
                products=products,
                store_name_map=store_name_map,
            )
            for review in reviews
        ],
        has_more=has_more,
    )


def moderate_admin_review(
    db: Session,
    current_user: User,
    review_id: str,
    payload: AdminReviewModerationUpdate,
) -> AdminReviewRead:
    require_admin(current_user)
    review = _get_admin_review(db, review_id)
    review.is_hidden = payload.is_hidden
    review.moderation_reason = payload.moderation_reason if payload.is_hidden else None
    review.moderated_at = datetime.now(UTC)
    review.moderated_by_user_id = current_user.id
    db.flush()
    recompute_product_review_metrics(db, review.product_id)
    db.commit()

    refreshed_review = _get_admin_review(db, str(review.id))
    products, store_name_map = _resolve_review_context(db, [refreshed_review])
    return _build_admin_review_read(
        refreshed_review,
        products=products,
        store_name_map=store_name_map,
    )


def list_admin_orders(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminOrderRead]:
    require_admin(current_user)
    statement = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
    )
    orders, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_order(db, order) for order in orders],
        has_more=has_more,
    )


def get_admin_order(db: Session, current_user: User, order_id: str) -> AdminOrderDetailRead:
    require_admin(current_user)
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.return_requests).selectinload(ReturnRequest.order_item),
            selectinload(Order.return_requests).selectinload(ReturnRequest.reviewed_by_user),
        )
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    return _serialize_order_detail(db, order)


def list_admin_return_requests(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminReturnRequestRead]:
    require_admin(current_user)
    statement = (
        select(ReturnRequest)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.items),
            selectinload(ReturnRequest.order).selectinload(Order.user),
            selectinload(ReturnRequest.order_item),
            selectinload(ReturnRequest.reviewed_by_user),
        )
        .order_by(ReturnRequest.created_at.desc())
    )
    requests, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_return_request(db, request) for request in requests],
        has_more=has_more,
    )


def get_admin_return_request(
    db: Session,
    current_user: User,
    request_id: str,
) -> AdminReturnRequestRead:
    require_admin(current_user)
    request = db.scalar(
        select(ReturnRequest)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.items),
            selectinload(ReturnRequest.order).selectinload(Order.user),
            selectinload(ReturnRequest.order_item),
            selectinload(ReturnRequest.reviewed_by_user),
        )
        .where(ReturnRequest.id == request_id)
    )
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Return request not found.",
        )

    return _serialize_return_request(db, request)


def update_admin_return_request(
    db: Session,
    current_user: User,
    request_id: str,
    payload: AdminReturnRequestUpdate,
) -> AdminReturnRequestRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_RETURN_REQUEST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported return request status.",
        )

    request = db.scalar(
        select(ReturnRequest)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.items),
            selectinload(ReturnRequest.order).selectinload(Order.user),
            selectinload(ReturnRequest.order).selectinload(Order.return_requests),
            selectinload(ReturnRequest.order_item),
            selectinload(ReturnRequest.reviewed_by_user),
        )
        .where(ReturnRequest.id == request_id)
    )
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Return request not found.",
        )

    request.status = payload.status
    request.admin_note = payload.admin_note
    request.reviewed_by_user_id = current_user.id
    request.reviewed_at = datetime.now(UTC)

    if payload.refund_amount is not None:
        request.refund_amount = round(payload.refund_amount, 2)
    elif payload.status == "refunded" and request.refund_amount is None:
        request.refund_amount = round(request.order_item.unit_price * request.quantity, 2)

    if payload.status in {"rejected", "refunded", "exchanged"}:
        request.resolved_at = datetime.now(UTC)
    else:
        request.resolved_at = None

    changed_wallet_vendor_id: uuid.UUID | None = None
    if payload.status == "refunded":
        changed_wallet_vendor_id = reverse_vendor_wallet_for_return_request(db, request)
        record_refund_adjustments(db, request)

    db.commit()
    db.refresh(request)

    status_copy = {
        "requested": "Return request reopened",
        "under_review": "Return request under review",
        "approved": "Return approved",
        "rejected": "Return request declined",
        "refunded": "Refund completed",
        "exchanged": "Exchange completed",
    }
    create_notification_event(
        db,
        request.order.user,
        kind="return_updated",
        title=status_copy.get(payload.status, "Return request updated"),
        body=f"{request.order_item.title}: {payload.status.replace('_', ' ')}.",
        icon="swap-horizontal-outline",
        accent="warning" if payload.status in {"requested", "under_review"} else "success" if payload.status in {"approved", "refunded", "exchanged"} else "warning",
        action_label="View order",
        route_type="order",
        route_target_id=str(request.order_id),
        image_key=request.order_item.image_key,
    )
    db.commit()
    db.refresh(request)
    if changed_wallet_vendor_id:
        publish_vendor_wallet_updates(changed_wallet_vendor_id)

    from app.controllers.order_controller import _broadcast_order_realtime

    _broadcast_order_realtime(db, request.order)
    return _serialize_return_request(db, request)


def update_admin_order_status(
    db: Session,
    current_user: User,
    order_id: str,
    payload: AdminOrderStatusUpdate,
) -> AdminOrderRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_ORDER_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported order status.")

    order = db.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")

    order.vendor_status = payload.status
    changed_wallet_vendor_ids: set[uuid.UUID] = set()
    if payload.status == "delivered":
        if order.payment_status != "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only paid orders can be marked as delivered.",
            )
        order.status = "delivered"
        order.delivered_at = datetime.now(UTC)
        order.cancelled_at = None
        order.cancellation_reason = None
        order.progress = 1
        order.tracking_eta = None
        changed_wallet_vendor_ids = settle_vendor_wallets_for_order(db, order)
    elif payload.status == "cancelled":
        order.status = "cancelled"
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = "Cancelled by admin"
        order.progress = 0
        order.tracking_eta = None
    elif payload.status == "out_for_delivery":
        order.status = "processing"
        order.progress = 0.9
        order.tracking_eta = "Out for delivery"
    else:
        order.status = "processing"
        progress_map = {
            "pending": 0.1,
            "confirmed": 0.2,
            "processing": 0.45,
            "ready": 0.75,
        }
        order.progress = progress_map.get(payload.status, order.progress)
        order.tracking_eta = payload.status.replace("_", " ").title()

    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    return _serialize_order(db, order)


def get_admin_finance_overview_payload(
    db: Session,
    current_user: User,
) -> AdminFinanceOverviewRead:
    return get_admin_finance_overview(db, current_user)


def list_admin_payment_transactions_payload(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
):
    return list_admin_payment_transactions(db, current_user, limit=limit, offset=offset)


def list_admin_platform_ledger_entries_payload(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
):
    return list_admin_platform_ledger_entries(db, current_user, limit=limit, offset=offset)


def list_admin_notifications(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminNotificationRead]:
    require_admin(current_user)
    statement = select(NotificationEvent).order_by(NotificationEvent.created_at.desc())
    notifications, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    read_keys = set(
        db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == current_user.id)
        ).all()
    )
    return AdminPageRead(
        items=[
            _serialize_notification(notification, is_read=str(notification.id) in read_keys)
            for notification in notifications
        ],
        has_more=has_more,
    )


def mark_admin_notification_read(
    db: Session,
    current_user: User,
    notification_id: str,
) -> NotificationMarkReadResponse:
    require_admin(current_user)
    notification = db.scalar(select(NotificationEvent).where(NotificationEvent.id == notification_id))
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")

    existing = db.scalar(
        select(NotificationRead).where(
            NotificationRead.user_id == current_user.id,
            NotificationRead.notification_key == str(notification.id),
        )
    )
    if not existing:
        db.add(
            NotificationRead(
                user_id=current_user.id,
                notification_key=str(notification.id),
            )
        )
        db.commit()

    return NotificationMarkReadResponse(notification_key=str(notification.id))
