from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.auth_controller import build_auth_token
from app.controllers.vendor_controller import (
    list_vendor_applications,
)
from app.core.auth import require_admin
from app.core.security import hash_password
from app.models import (
    NotificationEvent,
    NotificationRead,
    Order,
    Product,
    ReturnRequest,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
)
from app.models.chat import ChatThread, ChatThreadType, SupportChatStatus
from app.models.wallet import VendorWithdrawalRequest
from app.schemas.admin import (
    AdminBootstrapStatusRead,
    AdminDashboardRead,
    AdminDashboardStatsRead,
)
from app.schemas.user import AuthToken, UserCreate
from app.services.inventory_service import LOW_STOCK_THRESHOLD
from app.services.media_service import remove_media_file, save_image_upload

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






























































































def get_admin_bootstrap_status(db: Session) -> AdminBootstrapStatusRead:
    admin_count = db.scalar(
        select(func.count(User.id)).where(User.role == UserRole.ADMIN)
    ) or 0
    return AdminBootstrapStatusRead(bootstrap_enabled=admin_count == 0)


_BOOTSTRAP_ADMIN_LOCK_KEY = 927_331_001  # arbitrary constant, scoped to this one lock


def bootstrap_first_admin(db: Session, payload: UserCreate) -> AuthToken:
    # Serialize concurrent bootstrap attempts: without this, two requests
    # racing the admin_count==0 check during a fresh deploy could each pass
    # it and both mint a super-admin account. Held for the transaction, so
    # the second caller blocks here until the first commits (or rolls back).
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BOOTSTRAP_ADMIN_LOCK_KEY})

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
        admin_permission="super_admin",
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

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

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
        revenue_today=round(
            float(
                db.scalar(
                    select(func.coalesce(func.sum(Order.total_amount), 0.0)).where(
                        Order.created_at >= today_start
                    )
                )
                or 0.0
            ),
            2,
        ),
        orders_today=db.scalar(
            select(func.count(Order.id)).where(Order.created_at >= today_start)
        )
        or 0,
        pending_products=db.scalar(
            select(func.count(Product.id)).where(Product.status == "pending")
        )
        or 0,
        low_stock_products=db.scalar(
            select(func.count(Product.id)).where(
                Product.stock > 0,
                Product.stock <= LOW_STOCK_THRESHOLD,
                Product.status == "active",
            )
        )
        or 0,
        open_return_requests=db.scalar(
            select(func.count(ReturnRequest.id)).where(
                ReturnRequest.status.in_(["requested", "under_review", "approved"])
            )
        )
        or 0,
        support_waiting_on_admin=db.scalar(
            select(func.count(ChatThread.id)).where(
                ChatThread.thread_type == ChatThreadType.SUPPORT,
                ChatThread.support_status == SupportChatStatus.WAITING_ON_ADMIN,
            )
        )
        or 0,
        pending_withdrawals=db.scalar(
            select(func.count(VendorWithdrawalRequest.id)).where(
                VendorWithdrawalRequest.status.in_(["pending", "approved"])
            )
        )
        or 0,
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



























































































































































# Voucher administration now lives in app/controllers/admin/vouchers.py. These
# names are re-exported because routes/admin.py and several controllers import
# them from this module; moving the code should not force every caller to
# change its imports.


# Re-exported so every module and router that imports from here keeps working
# unchanged. The code now lives in app/controllers/admin/.


# Re-exported so every module and router importing from here keeps working
# unchanged. The implementations live in app/controllers/admin/.
from app.controllers.admin._shared import (  # noqa: E402,F401  (re-exported)
    _build_admin_review_read,
    _build_discount,
    _ensure_platform_store,
    _generate_store_id,
    _infer_image_key,
    _normalize_list,
    _payment_status,
    _resolve_review_context,
    _serialize_admin_review,
    _serialize_order,
    _serialize_return_request,
    _serialize_store_product,
    _serialize_user_payment_transaction,
    _serialize_user_store_summary,
    _slugify,
    _store_name_lookup,
    _sync_platform_store_avatar,
    _taxonomy_lookup_by_slug,
)
from app.controllers.admin.catalog import (  # noqa: E402,F401  (re-exported)
    _serialize_category,
    _serialize_market,
    _serialize_store,
    _serialize_store_detail,
    _store_activity_summary,
    broadcast_catalog_category_change,
    broadcast_catalog_market_change,
    create_admin_category,
    create_admin_market,
    create_admin_store,
    delete_admin_category,
    delete_admin_market,
    get_admin_store,
    list_admin_categories,
    list_admin_markets,
    list_admin_stores,
    update_admin_category,
    update_admin_market,
    update_admin_store_status,
)
from app.controllers.admin.commerce import (  # noqa: E402,F401  (re-exported)
    _get_admin_review,
    _notification_type,
    _serialize_notification,
    _serialize_order_detail,
    _serialize_order_item,
    compute_delivery_ops_snapshot,
    get_admin_finance_overview_payload,
    get_admin_order,
    get_admin_return_request,
    list_admin_delivery_ops,
    list_admin_notifications,
    list_admin_orders,
    list_admin_payment_transactions_payload,
    list_admin_platform_ledger_entries_payload,
    list_admin_return_requests,
    list_admin_reviews,
    mark_admin_notification_read,
    max_refund_amount_for,
    moderate_admin_review,
    update_admin_order_status,
    update_admin_return_request,
)
from app.controllers.admin.people import (  # noqa: E402,F401  (re-exported)
    _account_status,
    _count_super_admins,
    _serialize_saved_address,
    _serialize_saved_payment_method,
    _serialize_user,
    _serialize_user_detail,
    _serialize_vendor,
    _serialize_vendor_application_detail,
    _vendor_activity_summary,
    _vendor_application_by_user,
    create_admin_staff,
    get_admin_user,
    get_admin_vendor,
    list_admin_staff,
    list_admin_users,
    list_admin_vendors,
    login_admin_user,
    update_admin_user_permission,
    update_admin_user_status,
    update_admin_vendor_status,
)
from app.controllers.admin.products import (  # noqa: E402,F401  (re-exported)
    _generate_product_id,
    _get_store_for_admin_product,
    _resolve_product_taxonomy,
    _serialize_admin_products,
    _serialize_product,
    create_admin_product,
    get_admin_product,
    list_admin_products,
    update_admin_product,
    update_admin_product_status,
)
from app.controllers.admin.promotions import (  # noqa: E402,F401  (re-exported)
    _normalize_flash_event_slug,
    _replace_flash_sale_event_products,
    _serialize_flash_sale_event,
    _serialize_promo_banner,
    _validate_promo_banner_payload,
    archive_admin_flash_sale_event,
    archive_admin_promo_banner,
    broadcast_catalog_flash_sale_event_change,
    broadcast_catalog_promo_banner_change,
    create_admin_flash_sale_event,
    create_admin_promo_banner,
    get_admin_promo_banner,
    list_admin_flash_sale_events,
    list_admin_promo_banners,
    update_admin_flash_sale_event,
    update_admin_promo_banner,
)
from app.controllers.admin.vouchers import (  # noqa: E402,F401  (re-exported)
    _apply_voucher_upsert,
    _get_admin_voucher,
    _serialize_admin_vouchers,
    _serialize_voucher,
    _validate_voucher_payload,
    _voucher_audit_snapshot,
    _voucher_stats_map,
    _voucher_status,
    archive_admin_voucher,
    bulk_generate_admin_vouchers,
    create_admin_voucher,
    duplicate_admin_voucher,
    get_admin_promotion_analytics,
    list_admin_vouchers,
    pause_admin_voucher,
    resume_admin_voucher,
    review_admin_voucher,
    update_admin_voucher,
)
