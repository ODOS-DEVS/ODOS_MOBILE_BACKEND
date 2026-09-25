"""Admin view of everything after checkout: orders, returns, delivery
operations, reviews, payment transactions and notifications.
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.admin._shared import (
    _build_admin_review_read,
    _resolve_review_context,
    _serialize_order,
    _serialize_return_request,
    _store_name_lookup,
)
from app.controllers.finance_controller import (
    get_admin_finance_overview,
    list_admin_payment_transactions,
    list_admin_platform_ledger_entries,
)
from app.controllers.notification_controller import create_notification_event, order_notification_image
from app.controllers.review_controller import recompute_product_review_metrics
from app.core.admin_pagination import paginate_scalars
from app.core.auth import require_admin
from app.helpers.admin_audit import (
    log_admin_order_status_change,
    log_admin_return_resolution,
)
from app.models import (
    NotificationEvent,
    NotificationRead,
    Order,
    OrderItem,
    ReturnRequest,
    Review,
    User,
)
from app.schemas.admin import (
    AdminDeliveryOpsOrderRead,
    AdminDeliveryOpsRead,
    AdminNotificationRead,
    AdminOrderDetailRead,
    AdminOrderItemRead,
    AdminOrderRead,
    AdminOrderStatusUpdate,
    AdminReturnRequestRead,
    AdminReturnRequestUpdate,
    AdminReviewModerationUpdate,
    AdminReviewRead,
    NotificationMarkReadResponse,
)
from app.schemas.order import OrderStatusEventRead
from app.schemas.pagination import AdminPageRead
from app.schemas.payment import (
    AdminFinanceOverviewRead,
)
from app.services.delivery_lifecycle_service import admin_override_deliver, dispatch_order
from app.services.delivery_service import delivery_method_label, get_delivery_config
from app.services.order_timeline_service import record_order_status_event
from app.services.push_service import (
    customer_order_status_push_copy,
    dispatch_customer_order_push,
)
from app.services.return_request_service import (
    ReturnRequestError,
    apply_return_request_status_change,
)

DELIVERY_OPS_ACTIVE_STATUSES = ("pending", "confirmed", "processing", "ready", "out_for_delivery")

# How long an order may sit in a given stage before dispatch should flag it as
# stuck. Tuned for a vendor-fulfilled marketplace (no dedicated rider fleet),
# so "processing" (the vendor packing it) gets the most slack.
DELIVERY_OPS_STAGE_SLA_MINUTES = {
    "pending": 30,
    "confirmed": 45,
    "processing": 120,
    "ready": 30,
    "out_for_delivery": 90,
}

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


def max_refund_amount_for(unit_price: float, quantity: int) -> float:
    """The most a return's refund_amount can legitimately be — the line
    item's own value. Extracted as a pure function so the cap itself
    (not just the surrounding endpoint) is directly unit-testable."""
    return round(unit_price * quantity, 2)


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
        delivery_status=order.delivery_status,
        dispatched_at=order.dispatched_at,
        confirmation_method=order.confirmation_method,
        delivery_problem_reason=order.delivery_problem_reason,
        delivery_problem_reported_at=order.delivery_problem_reported_at,
        auto_release_at=order.auto_release_at,
        settlement_status=order.settlement_status,
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
        timeline=[OrderStatusEventRead.model_validate(event) for event in order.timeline],
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


def compute_delivery_ops_snapshot(db: Session) -> AdminDeliveryOpsRead:
    statement = (
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.timeline))
        .where(
            Order.vendor_status.in_(DELIVERY_OPS_ACTIVE_STATUSES),
            Order.payment_status == "paid",
        )
        .order_by(Order.created_at.asc())
    )
    orders = list(db.scalars(statement).all())
    now = datetime.now(UTC)
    stage_counts: dict[str, int] = dict.fromkeys(DELIVERY_OPS_ACTIVE_STATUSES, 0)
    delayed_count = 0
    exceptions_count = 0
    rows: list[AdminDeliveryOpsOrderRead] = []

    for order in orders:
        stage_counts[order.vendor_status] = stage_counts.get(order.vendor_status, 0) + 1
        stage_started_at = order.timeline[-1].occurred_at if order.timeline else order.created_at
        minutes_in_stage = max(0, int((now - stage_started_at).total_seconds() // 60))
        sla = DELIVERY_OPS_STAGE_SLA_MINUTES.get(order.vendor_status)
        is_delayed = bool(sla and minutes_in_stage > sla)
        is_exception = order.delivery_status == "customer_problem"
        if is_delayed:
            delayed_count += 1
        if is_exception:
            exceptions_count += 1

        rows.append(
            AdminDeliveryOpsOrderRead(
                id=order.id,
                order_number=order.order_number,
                customer_name=order.address_full_name,
                store_name=_store_name_lookup(db, order),
                vendor_status=order.vendor_status,
                delivery_status=order.delivery_status,
                settlement_status=order.settlement_status,
                delivery_method=order.delivery_method,
                address_city=order.address_city,
                address_region=order.address_region,
                product_count=sum(item.quantity for item in order.items),
                total_amount=round(order.total_amount, 2),
                stage_started_at=stage_started_at,
                minutes_in_stage=minutes_in_stage,
                is_delayed=is_delayed,
                is_exception=is_exception,
                placed_at=order.placed_at,
            )
        )

    # Exceptions first, then delayed, then by longest-in-stage — the orders
    # that need a human are always what admins see first.
    rows.sort(key=lambda row: (not row.is_exception, not row.is_delayed, -row.minutes_in_stage))
    return AdminDeliveryOpsRead(
        orders=rows,
        stage_counts=stage_counts,
        delayed_count=delayed_count,
        exceptions_count=exceptions_count,
        total_active=len(rows),
    )


def list_admin_delivery_ops(db: Session, current_user: User) -> AdminDeliveryOpsRead:
    require_admin(current_user)
    return compute_delivery_ops_snapshot(db)


def get_admin_order(db: Session, current_user: User, order_id: str) -> AdminOrderDetailRead:
    require_admin(current_user)
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.return_requests).selectinload(ReturnRequest.order_item),
            selectinload(Order.return_requests).selectinload(ReturnRequest.reviewed_by_user),
            selectinload(Order.timeline),
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

    # Cheap unlocked read just to size the refund ceiling — unit_price and
    # quantity never change after the request is created, so a stale read
    # here carries no race risk. The shared function re-enforces this cap
    # under its own lock as the authoritative check.
    pricing_row = db.execute(
        select(OrderItem.unit_price, ReturnRequest.quantity)
        .join(ReturnRequest, ReturnRequest.order_item_id == OrderItem.id)
        .where(ReturnRequest.id == request_id)
    ).first()
    if not pricing_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Return request not found.",
        )
    max_refund_amount = max_refund_amount_for(pricing_row.unit_price, pricing_row.quantity)

    # Captured before the change: apply_return_request_status_change overwrites
    # status in place, so reading it afterwards would log the new value twice.
    before_status = db.scalar(select(ReturnRequest.status).where(ReturnRequest.id == request_id))

    try:
        result = apply_return_request_status_change(
            db,
            request_id,
            status=payload.status,
            note=payload.admin_note,
            refund_amount=payload.refund_amount,
            reviewed_by_user_id=current_user.id,
            max_refund_amount=max_refund_amount,
            actor_role="admin",
            waive_return=payload.waive_return,
            received_condition_note=payload.received_condition_note,
        )
    except ReturnRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Only the transitions that cost somebody money are worth an audit entry;
    # logging every note edit would bury them.
    if payload.status in {"approved", "refunded", "exchanged", "rejected"}:
        log_admin_return_resolution(
            db,
            admin_user=current_user,
            return_request_id=str(request_id),
            order_number=result.request.order.order_number,
            before_status=before_status or "unknown",
            after_status=payload.status,
            refund_amount=result.request.refund_amount,
            waived=bool(result.request.return_waived),
        )
        db.commit()

    return _serialize_return_request(db, result.request)


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
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")

    before_status = order.vendor_status

    if payload.status == "delivered":
        # Delegated in full: state validation, the mandatory-reason rule,
        # settlement, and the customer notification all live in
        # delivery_lifecycle_service so this admin path can't drift from the
        # customer-confirm / auto-release completion paths.
        order, _changed_wallet_vendor_ids = admin_override_deliver(
            db, current_user, order_id, reason=payload.note or ""
        )
        db.commit()
        db.refresh(order)

        from app.controllers.order_controller import _broadcast_order_realtime

        _broadcast_order_realtime(db, order)
        log_admin_order_status_change(
            db,
            admin_user=current_user,
            order_id=str(order.id),
            order_number=order.order_number,
            before_status=before_status,
            after_status=payload.status,
        )
        return _serialize_order(db, order)

    order.vendor_status = payload.status
    if payload.status == "cancelled":
        order.status = "cancelled"
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = "Cancelled by admin"
        order.progress = 0
        order.tracking_eta = None
    elif payload.status == "out_for_delivery":
        dispatch_order(db, order, actor=current_user)
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

    if payload.status != "out_for_delivery":
        # dispatch_order already recorded its own (richer) DISPATCHED/
        # REDISPATCHED event above — don't double up.
        record_order_status_event(
            db,
            order,
            status=payload.status,
            actor_role="admin",
            actor_id=current_user.id,
            note=payload.note or ("Cancelled by admin" if payload.status == "cancelled" else None),
        )

    push_title, push_body = customer_order_status_push_copy(
        order_number=order.order_number,
        vendor_status=payload.status,
        tracking_eta=order.tracking_eta,
    )
    preview = order_notification_image(order)
    status_event = create_notification_event(
        db,
        order.user,
        kind="vendor_order_update",
        title=push_title,
        body=push_body,
        icon="bag-handle-outline",
        accent="warning" if payload.status == "cancelled" else "neutral",
        action_label="Track order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )
    dispatch_customer_order_push(
        user=order.user,
        title=push_title,
        body=push_body,
        order=order,
        notification_event=status_event,
    )
    db.commit()
    db.refresh(order)

    from app.controllers.order_controller import _broadcast_order_realtime

    _broadcast_order_realtime(db, order)
    log_admin_order_status_change(
        db,
        admin_user=current_user,
        order_id=str(order.id),
        order_number=order.order_number,
        before_status=before_status,
        after_status=payload.status,
    )
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
