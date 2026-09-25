"""Admin view of the people on the platform: customers, vendor applications
and admin staff, including status changes and permission grants.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.admin._shared import (
    _build_admin_review_read,
    _resolve_review_context,
    _serialize_order,
    _serialize_return_request,
    _serialize_user_payment_transaction,
    _serialize_user_store_summary,
)
from app.controllers.auth_controller import login_user
from app.core.admin_pagination import paginate_scalars
from app.core.admin_permissions import AdminPermissionLevel, require_super_admin
from app.core.auth import require_admin
from app.core.event_types import USER_LOGIN
from app.core.security import hash_password
from app.helpers.admin_audit import (
    log_admin_role_change,
    log_admin_user_status_change,
    log_admin_vendor_status_change,
)
from app.models import (
    CustomerWallet,
    Order,
    PaymentTransaction,
    Product,
    ReturnRequest,
    SavedAddress,
    SavedPaymentMethod,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
)
from app.models.user_behavior import UserBehaviorEvent
from app.schemas.admin import (
    AdminPermissionUpdate,
    AdminStaffCreate,
    AdminUserAddressRead,
    AdminUserCartItemRead,
    AdminUserDetailRead,
    AdminUserNotificationRead,
    AdminUserPaymentMethodRead,
    AdminUserRead,
    AdminUserStatsRead,
    AdminUserStatusUpdate,
    AdminUserVendorApplicationRead,
    AdminUserWalletSummaryRead,
    AdminUserWishlistItemRead,
    AdminVendorRead,
    AdminVendorStatusUpdate,
)
from app.schemas.pagination import AdminPageRead
from app.schemas.user import AuthToken, UserLogin
from app.services.event_log_service import record_admin_event
from app.services.finance_math import round_money

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


def _account_status(user: User) -> str:
    return "active" if user.is_active else "blocked"


def _serialize_user(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        avatar_url=user.avatar_url,
        roles=user.roles,
        admin_permission=getattr(user, "admin_permission", None),
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


def login_admin_user(db: Session, credentials: UserLogin, request=None) -> AuthToken:
    session = login_user(db, credentials, request=request)
    if session.user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account does not have admin access.",
        )
    record_admin_event(
        db,
        admin_user=session.user,
        event_type=USER_LOGIN,
        action="admin.login",
        entity_type="user",
        entity_id=str(session.user.id),
        metadata={"email": session.user.email},
    )
    return session


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

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own account status.",
        )

    if user.role == UserRole.ADMIN:
        from app.core.admin_permissions import (
            AdminPermissionLevel,
            resolve_admin_permission,
        )

        if resolve_admin_permission(current_user) != AdminPermissionLevel.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a super admin can change another admin's account status.",
            )

    before_active = user.is_active
    user.is_active = payload.account_status == "active"
    if not user.is_active:
        user.token_version = int(getattr(user, "token_version", 0) or 0) + 1
    db.commit()
    db.refresh(user)
    log_admin_user_status_change(
        db,
        admin_user=current_user,
        target_user=user,
        before_active=before_active,
        after_active=user.is_active,
    )
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

    before_status = vendor.vendor_status.value
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
    log_admin_vendor_status_change(
        db,
        admin_user=current_user,
        vendor=vendor,
        before_status=before_status,
        after_status=vendor.vendor_status.value,
    )
    return _serialize_vendor(db, vendor)


def _count_super_admins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.ADMIN,
                User.admin_permission == AdminPermissionLevel.SUPER_ADMIN.value,
            )
        )
        or 0
    )


def list_admin_staff(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminUserRead]:
    require_super_admin(current_user)
    statement = (
        select(User)
        .where(User.role == UserRole.ADMIN)
        .order_by(User.created_at.desc())
    )
    users, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_user(user) for user in users],
        has_more=has_more,
    )


def create_admin_staff(
    db: Session,
    current_user: User,
    payload: AdminStaffCreate,
) -> AdminUserRead:
    require_super_admin(current_user)

    try:
        permission = AdminPermissionLevel(payload.admin_permission)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported admin permission level.",
        ) from exc

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
        full_name=payload.full_name.strip(),
        email=normalized_email,
        phone_number=payload.phone_number,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN,
        admin_permission=permission.value,
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

    log_admin_role_change(
        db,
        admin_user=current_user,
        target_user=user,
        before_permission=None,
        after_permission=permission.value,
    )
    return _serialize_user(user)


def update_admin_user_permission(
    db: Session,
    current_user: User,
    user_id: str,
    payload: AdminPermissionUpdate,
) -> AdminUserRead:
    require_super_admin(current_user)

    try:
        permission = AdminPermissionLevel(payload.admin_permission)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported admin permission level.",
        ) from exc

    user = db.scalar(select(User).where(User.id == user_id))
    if not user or user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found.")

    before_permission = getattr(user, "admin_permission", None)
    if (
        user.id == current_user.id
        and before_permission == AdminPermissionLevel.SUPER_ADMIN.value
        and permission != AdminPermissionLevel.SUPER_ADMIN
        and _count_super_admins(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove the last super admin permission from your account.",
        )

    user.admin_permission = permission.value
    db.commit()
    db.refresh(user)
    log_admin_role_change(
        db,
        admin_user=current_user,
        target_user=user,
        before_permission=before_permission,
        after_permission=permission.value,
    )
    return _serialize_user(user)
