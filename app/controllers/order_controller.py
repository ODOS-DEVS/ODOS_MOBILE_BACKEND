import secrets

from datetime import datetime, timezone
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.controllers.vendor_controller import fetch_vendor_dashboard, list_vendor_orders_payloads
from app.controllers.voucher_controller import build_voucher_quote
from app.models import CartItem, Order, OrderItem, Product, ReturnRequest, User, VoucherRedemption
from app.schemas.order import OrderCreate, OrderRead, ReturnRequestCreate, ReturnRequestRead
from app.services.realtime_service import realtime_manager
from app.services.push_service import send_expo_push_notification

logger = logging.getLogger(__name__)
OPEN_RETURN_REQUEST_STATUSES = {"requested", "under_review", "approved"}


def _serialize_order(order: Order) -> dict:
    return OrderRead.model_validate(order).model_dump(mode="json")


def _serialize_return_request(request: ReturnRequest) -> ReturnRequestRead:
    return ReturnRequestRead.model_validate(request)


def _broadcast_order_realtime(db: Session, order: Order) -> None:
    realtime_manager.publish_user_event_sync(
        str(order.user_id),
        "order.updated",
        _serialize_order(order),
    )

    vendor_user_ids = list(
        dict.fromkeys(
            str(vendor_user_id)
            for vendor_user_id in db.scalars(
                select(Product.vendor_user_id).where(
                    Product.id.in_([item.product_id for item in order.items if item.product_id]),
                    Product.vendor_user_id.is_not(None),
                )
            ).all()
            if vendor_user_id
        )
    )

    for vendor_user_id in vendor_user_ids:
        vendor_user = db.get(User, vendor_user_id)
        if not vendor_user:
            continue

        vendor_order = next(
            (
                item
                for item in list_vendor_orders_payloads(db, vendor_user)
                if str(item.id) == str(order.id)
            ),
            None,
        )
        if vendor_order:
            realtime_manager.publish_user_event_sync(
                str(vendor_user.id),
                "vendor.order.updated",
                vendor_order.model_dump(mode="json"),
            )

        try:
            dashboard = fetch_vendor_dashboard(db, vendor_user)
        except HTTPException:
            dashboard = None

        if dashboard:
            realtime_manager.publish_user_event_sync(
                str(vendor_user.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )


def _generate_order_number(db: Session) -> str:
    for _ in range(10):
        candidate = f"ORD-{secrets.randbelow(900000) + 100000}"
        existing = db.scalar(select(Order.id).where(Order.order_number == candidate))
        if not existing:
            return candidate

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="We couldn't create an order number right now.",
    )


def _dispatch_order_push(
    *,
    user: User,
    title: str,
    body: str,
    order: Order,
) -> None:
    try:
        send_expo_push_notification(
            user=user,
            title=title,
            body=body,
            data={
                "type": "order_update",
                "orderId": str(order.id),
                "status": order.status,
            },
        )
    except Exception:
        logger.exception("Failed to send order push for %s", order.id)


def create_order(db: Session, user: User, payload: OrderCreate) -> Order:
    computed_subtotal = 0.0
    voucher_quote = None
    product_returnability_map = {
        product_id: bool(is_returnable)
        for product_id, is_returnable in db.execute(
            select(Product.id, Product.is_returnable).where(
                Product.id.in_([item.product_id for item in payload.items]),
            )
        ).all()
    }
    if payload.voucher_code:
        voucher_quote = build_voucher_quote(
            db,
            user,
            payload.voucher_code,
            payload.items,
            payload.shipping_amount,
        )

    order = Order(
        order_number=_generate_order_number(db),
        user_id=user.id,
        source=payload.source,
        status="processing",
        subtotal_amount=payload.subtotal_amount,
        shipping_amount=payload.shipping_amount,
        total_amount=payload.total_amount,
        progress=0.18,
        tracking_eta="Estimated delivery in 2–3 days",
        address_full_name=payload.address_full_name,
        address_phone=payload.address_phone,
        address_street=payload.address_street,
        address_city=payload.address_city,
        address_region=payload.address_region,
        payment_type=payload.payment_type,
        payment_label=payload.payment_label,
        payment_network=payload.payment_network,
        payment_phone=payload.payment_phone,
        payment_last4=payload.payment_last4,
        voucher_id=voucher_quote.voucher.id if voucher_quote else None,
        voucher_code=voucher_quote.voucher.code if voucher_quote else None,
        voucher_title=voucher_quote.voucher.title if voucher_quote else None,
        discount_amount=voucher_quote.discount_amount if voucher_quote else 0,
    )

    for item in payload.items:
        line_total = round(item.unit_price * item.quantity, 2)
        computed_subtotal += line_total
        order.items.append(
            OrderItem(
                product_id=item.product_id,
                title=item.title,
                category=item.category,
                image_url=item.image_url,
                image_key=item.image_key,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=line_total,
                is_returnable=product_returnability_map.get(item.product_id, True),
                selected_color=item.selected_color,
                selected_size=item.selected_size,
            )
        )

    computed_subtotal = round(computed_subtotal, 2)
    computed_discount = round(voucher_quote.discount_amount if voucher_quote else 0, 2)
    computed_total = round(computed_subtotal + payload.shipping_amount - computed_discount, 2)

    if (
        abs(computed_subtotal - payload.subtotal_amount) > 0.01
        or abs(computed_discount - payload.discount_amount) > 0.01
        or abs(computed_total - payload.total_amount) > 0.01
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The order totals didn't match the selected items.",
        )

    order.subtotal_amount = computed_subtotal
    order.shipping_amount = round(payload.shipping_amount, 2)
    order.discount_amount = computed_discount
    order.total_amount = computed_total

    db.add(order)
    db.flush()

    if voucher_quote:
        db.add(
            VoucherRedemption(
                voucher_id=voucher_quote.voucher.id,
                user_id=user.id,
                order_id=order.id,
                voucher_code=voucher_quote.voucher.code,
                discount_amount=voucher_quote.discount_amount,
            )
        )

    if payload.source == "cart":
        cart_items = list(db.scalars(select(CartItem).where(CartItem.user_id == user.id)).all())
        for cart_item in cart_items:
            db.delete(cart_item)

    db.commit()

    created_order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.return_requests))
        .where(Order.id == order.id, Order.user_id == user.id)
    )
    if not created_order:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your order was placed, but we couldn't reload it.",
        )

    _dispatch_order_push(
        user=user,
        title="Order placed successfully",
        body=f"Order #{created_order.order_number} is now being prepared.",
        order=created_order,
    )
    create_notification_event(
        db,
        user,
        kind="order_placed",
        title="Order placed successfully",
        body=f"Order #{created_order.order_number} is now being prepared for delivery.",
        icon="bag-handle-outline",
        accent="neutral",
        action_label="Track order",
        route_type="order",
        route_target_id=str(created_order.id),
        image_key=created_order.items[0].image_key if created_order.items else None,
    )
    db.commit()
    db.refresh(created_order)
    _broadcast_order_realtime(db, created_order)

    return created_order


def list_orders(db: Session, user: User) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.return_requests))
            .where(Order.user_id == user.id)
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
        ).all()
    )


def get_order(db: Session, user: User, order_id: str) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.return_requests))
        .where(Order.id == order_id, Order.user_id == user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found.",
        )

    return order


def create_return_request(
    db: Session,
    user: User,
    order_id: str,
    payload: ReturnRequestCreate,
) -> ReturnRequestRead:
    order = get_order(db, user, order_id)

    if order.status != "delivered":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Returns can only be requested after an order has been delivered.",
        )

    order_item = next((item for item in order.items if item.id == payload.order_item_id), None)
    if not order_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order item was not found.",
        )

    if not order_item.is_returnable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This product is not eligible for returns or exchanges.",
        )

    if payload.quantity > order_item.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Return quantity cannot be greater than the delivered quantity.",
        )

    existing_open_request = db.scalar(
        select(ReturnRequest).where(
            ReturnRequest.order_item_id == order_item.id,
            ReturnRequest.user_id == user.id,
            ReturnRequest.status.in_(OPEN_RETURN_REQUEST_STATUSES),
        )
    )
    if existing_open_request:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There is already an active return request for this item.",
        )

    refund_amount = None
    if payload.request_type == "refund":
        refund_amount = round(order_item.unit_price * payload.quantity, 2)

    return_request = ReturnRequest(
        order_id=order.id,
        order_item_id=order_item.id,
        user_id=user.id,
        request_type=payload.request_type,
        status="requested",
        quantity=payload.quantity,
        reason=payload.reason,
        details=payload.details,
        evidence_image_urls=payload.evidence_image_urls,
        refund_amount=refund_amount,
    )
    db.add(return_request)
    db.commit()

    refreshed_request = db.scalar(
        select(ReturnRequest)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.items),
            selectinload(ReturnRequest.order).selectinload(Order.return_requests),
        )
        .where(ReturnRequest.id == return_request.id)
    )
    if not refreshed_request:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your return request was created, but we couldn't reload it.",
        )

    create_notification_event(
        db,
        user,
        kind="return_requested",
        title="Return request submitted",
        body=f"We've received your {payload.request_type} request for {order_item.title}.",
        icon="refresh-circle-outline",
        accent="warning",
        action_label="View order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=order_item.image_key,
    )
    db.commit()
    db.refresh(refreshed_request)
    _broadcast_order_realtime(db, refreshed_request.order)
    return _serialize_return_request(refreshed_request)


def cancel_order(
    db: Session,
    user: User,
    order_id: str,
    *,
    reason: str = "Cancelled by customer",
) -> Order:
    order = get_order(db, user, order_id)

    if order.status != "processing":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only processing orders can be cancelled.",
        )

    order.status = "cancelled"
    order.progress = 0
    order.tracking_eta = None
    order.cancellation_reason = reason
    order.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    _dispatch_order_push(
        user=user,
        title="Order cancelled",
        body=f"Order #{order.order_number} has been cancelled.",
        order=order,
    )
    create_notification_event(
        db,
        user,
        kind="order_cancelled",
        title="Order cancelled",
        body=reason or f"Order #{order.order_number} has been cancelled.",
        icon="close-circle-outline",
        accent="warning",
        action_label="Review order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=order.items[0].image_key if order.items else None,
    )
    db.commit()
    db.refresh(order)
    _broadcast_order_realtime(db, order)
    return order


def confirm_order_delivery(db: Session, user: User, order_id: str) -> Order:
    order = get_order(db, user, order_id)

    if order.status != "processing":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only processing orders can be marked as delivered.",
        )

    order.status = "delivered"
    order.progress = 1
    order.tracking_eta = None
    order.cancellation_reason = None
    order.cancelled_at = None
    order.delivered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    _dispatch_order_push(
        user=user,
        title="Order delivered",
        body=f"Order #{order.order_number} has arrived successfully.",
        order=order,
    )
    create_notification_event(
        db,
        user,
        kind="order_delivered",
        title="Order delivered",
        body=f"Order #{order.order_number} has arrived successfully.",
        icon="checkmark-done-outline",
        accent="success",
        action_label="View receipt",
        route_type="order",
        route_target_id=str(order.id),
        image_key=order.items[0].image_key if order.items else None,
    )
    db.commit()
    db.refresh(order)
    _broadcast_order_realtime(db, order)
    return order


def delete_order(db: Session, user: User, order_id: str) -> None:
    order = get_order(db, user, order_id)

    if order.status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only cancelled orders can be removed.",
        )

    db.delete(order)
    db.commit()
