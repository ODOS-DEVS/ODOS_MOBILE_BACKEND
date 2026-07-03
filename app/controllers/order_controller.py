import secrets

from datetime import datetime, timezone
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event, order_notification_image
from app.controllers.vendor_controller import fetch_vendor_dashboard, list_vendor_orders_payloads
from app.services.promotion_engine import calculate_best_discount
from app.helpers.promo_audit import log_promo_applied, log_promo_rejections
from app.core.event_types import ORDER_CREATED
from app.models import CartItem, NotificationEvent, Order, OrderItem, Product, ReturnRequest, User, VoucherRedemption
from app.services.event_log_service import record_user_event
from app.schemas.order import OrderCreate, OrderItemCreate, OrderRead, ReturnRequestCreate, ReturnRequestRead
from app.services.pricing_service import compute_server_subtotal
from app.services.delivery_service import (
    get_delivery_config,
    tracking_eta_after_payment,
    validate_delivery_checkout,
)
from app.services.finance_math import round_money
from app.services.realtime_service import realtime_manager
from app.services.push_service import build_push_data, send_expo_push_notification, send_vendor_order_push
from app.services.sms_service import send_order_payment_confirmation_sms

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


def _order_vendor_users(db: Session, order: Order) -> list[User]:
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

    vendors: list[User] = []
    for vendor_user_id in vendor_user_ids:
        vendor_user = db.get(User, vendor_user_id)
        if vendor_user:
            vendors.append(vendor_user)
    return vendors


def _dispatch_vendor_new_order_alerts(db: Session, order: Order) -> None:
    preview = order_notification_image(order)

    for vendor_user in _order_vendor_users(db, order):
        vendor_order = next(
            (
                item
                for item in list_vendor_orders_payloads(db, vendor_user)
                if str(item.id) == str(order.id)
            ),
            None,
        )
        if not vendor_order:
            continue

        item_label = "item" if vendor_order.product_count == 1 else "items"
        amount_label = f"GHS {vendor_order.total_amount:,.2f}"
        notification_body = (
            f"Order #{order.order_number} · {vendor_order.product_count} {item_label} · "
            f"{amount_label}. Tap to fulfil now."
        )

        try:
            vendor_event = create_notification_event(
                db,
                vendor_user,
                kind="vendor_order_received",
                title="New order received",
                body=notification_body,
                icon="receipt-outline",
                accent="warning",
                action_label="View order",
                route_type="vendor_order",
                route_target_id=str(order.id),
                image_key=preview["image_key"],
                image_url=preview["image_url"],
            )
            send_vendor_order_push(
                user=vendor_user,
                title="New order on ODOS",
                body=f"#{order.order_number} · {vendor_order.product_count} {item_label} · {amount_label}",
                data=build_push_data(
                    push_type="vendor_order",
                    route_type="vendor_order",
                    route_target_id=str(order.id),
                    notification_event=vendor_event,
                    extra={
                        "orderId": str(order.id),
                        "orderNumber": order.order_number,
                        "productCount": vendor_order.product_count,
                        "totalAmount": vendor_order.total_amount,
                        "alertKind": "new",
                    },
                ),
            )
        except Exception:
            logger.exception(
                "Failed to send vendor new-order alert for order %s to vendor %s",
                order.id,
                vendor_user.id,
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
    notification_event: NotificationEvent | None = None,
) -> None:
    try:
        send_expo_push_notification(
            user=user,
            title=title,
            body=body,
            data=build_push_data(
                push_type="order_update",
                route_type="order",
                route_target_id=str(order.id),
                notification_event=notification_event,
                extra={
                    "orderId": str(order.id),
                    "status": order.status,
                },
            ),
        )
    except Exception:
        logger.exception("Failed to send order push for %s", order.id)


def _validate_checkout_items(db: Session, items: list[OrderItemCreate]) -> None:
    product_ids = [item.product_id for item in items]
    if not product_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your order must include at least one item.",
        )

    products = {
        product.id: product
        for product in db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    }

    for item in items:
        product = products.get(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more items are no longer available.",
            )
        if not product.is_active or product.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{item.title} is no longer available.",
            )
        if product.stock < item.quantity:
            remaining = max(product.stock, 0)
            detail = (
                f"Only {remaining} left in stock for {item.title}."
                if remaining
                else f"{item.title} is out of stock."
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            )


def _decrement_order_inventory(db: Session, order: Order) -> None:
    product_ids = [item.product_id for item in order.items if item.product_id]
    if not product_ids:
        return

    products = {
        product.id: product
        for product in db.scalars(
            select(Product).where(Product.id.in_(product_ids)).with_for_update()
        ).all()
    }

    for item in order.items:
        if not item.product_id:
            continue
        product = products.get(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{item.title} is no longer available.",
            )
        if product.stock < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Not enough stock left for {item.title}.",
            )
        product.stock -= item.quantity


def _load_product_snapshot_map(db: Session, payload: OrderCreate) -> dict[str, dict[str, object | None]]:
    return {
        product_id: {
            "is_returnable": bool(is_returnable),
            "vendor_user_id": vendor_user_id,
            "store_id": store_id,
            "image_url": image_url,
            "image_key": image_key,
        }
        for product_id, is_returnable, vendor_user_id, store_id, image_url, image_key in db.execute(
            select(
                Product.id,
                Product.is_returnable,
                Product.vendor_user_id,
                Product.store_id,
                Product.image_url,
                Product.image_key,
            ).where(Product.id.in_([item.product_id for item in payload.items]))
        ).all()
    }


def _validate_order_totals(
    payload: OrderCreate,
    *,
    computed_subtotal: float,
    computed_discount: float,
) -> float:
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
    return computed_total


def prepare_order_for_checkout(
    db: Session,
    user: User,
    payload: OrderCreate,
    *,
    payment_provider: str = "paystack",
    payment_reference: str | None = None,
) -> Order:
    computed_subtotal = 0.0
    promo_result = None
    _validate_checkout_items(db, payload.items)
    product_snapshot_map = _load_product_snapshot_map(db, payload)
    try:
        server_subtotal, resolved_prices = compute_server_subtotal(db, payload.items)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    for item in payload.items:
        pricing = resolved_prices.get(item.product_id)
        if pricing and abs(pricing.sale_price - item.unit_price) > 0.01:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Some item prices changed. Refresh your cart and try again.",
            )

    promo_result = calculate_best_discount(
        db,
        user,
        payload.items,
        payload.shipping_amount,
        voucher_code=payload.voucher_code,
        include_auto_apply=True,
    )
    if payload.voucher_code and promo_result.rejected_promotions:
        log_promo_rejections(
            db,
            user=user,
            rejections=promo_result.rejected_promotions,
            voucher_code=payload.voucher_code,
        )
    if promo_result.applied_promotions:
        log_promo_applied(db, user=user, order=None, result=promo_result)

    computed_discount = round(promo_result.discount_amount, 2)
    primary_voucher = promo_result.primary_voucher

    try:
        delivery_config = get_delivery_config(db)
        delivery_method, validated_shipping = validate_delivery_checkout(
            subtotal=server_subtotal,
            region=payload.address_region,
            delivery_method=payload.delivery_method,
            shipping_amount=payload.shipping_amount,
            config=delivery_config,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    order = Order(
        order_number=_generate_order_number(db),
        user_id=user.id,
        source=payload.source,
        status="pending_payment",
        subtotal_amount=payload.subtotal_amount,
        shipping_amount=validated_shipping,
        delivery_method=delivery_method,
        total_amount=payload.total_amount,
        progress=0.03,
        tracking_eta="Awaiting payment confirmation",
        address_full_name=payload.address_full_name,
        address_phone=payload.address_phone,
        address_street=payload.address_street,
        address_city=payload.address_city,
        address_region=payload.address_region,
        payment_type=payload.payment_type,
        payment_label=payload.payment_label,
        payment_status="pending",
        payment_provider=payment_provider,
        payment_reference=payment_reference,
        payment_network=payload.payment_network,
        payment_phone=payload.payment_phone,
        payment_last4=payload.payment_last4,
        voucher_id=primary_voucher.id if primary_voucher else None,
        voucher_code=primary_voucher.code if primary_voucher else None,
        voucher_title=primary_voucher.title if primary_voucher else None,
        discount_amount=computed_discount,
        promotion_breakdown=promo_result.to_breakdown_json() if promo_result.applied_promotions else None,
    )

    for item in payload.items:
        line_total = round(item.unit_price * item.quantity, 2)
        computed_subtotal += line_total
        product_snapshot = product_snapshot_map.get(item.product_id, {})
        image_url = item.image_url or product_snapshot.get("image_url")
        image_key = item.image_key or product_snapshot.get("image_key")
        order.items.append(
            OrderItem(
                product_id=item.product_id,
                title=item.title,
                category=item.category,
                image_url=image_url,
                image_key=image_key,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=line_total,
                is_returnable=product_snapshot_map.get(item.product_id, {}).get(
                    "is_returnable",
                    True,
                ),
                vendor_user_id=product_snapshot_map.get(item.product_id, {}).get(
                    "vendor_user_id"
                ),
                store_id=product_snapshot_map.get(item.product_id, {}).get(
                    "store_id"
                ),
                selected_color=item.selected_color,
                selected_size=item.selected_size,
            )
        )

    computed_subtotal = round(computed_subtotal, 2)
    if abs(computed_subtotal - server_subtotal) > 0.02:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The order totals didn't match the selected items.",
        )
    computed_total = _validate_order_totals(
        payload,
        computed_subtotal=computed_subtotal,
        computed_discount=computed_discount,
    )
    order.subtotal_amount = computed_subtotal
    order.shipping_amount = round(validated_shipping, 2)
    order.delivery_method = delivery_method
    order.discount_amount = computed_discount
    order.total_amount = computed_total

    db.add(order)
    db.flush()
    return order


def _maybe_send_order_payment_confirmation_sms(user: User, order: Order) -> None:
    if not user.phone_verified or not user.phone_number:
        return

    try:
        send_order_payment_confirmation_sms(
            phone_number=user.phone_number,
            order_number=order.order_number,
            total_amount=order.total_amount,
            payment_label=order.payment_label,
        )
    except Exception:
        logger.exception(
            "Failed to send order payment confirmation SMS for order %s",
            order.id,
        )


def activate_order_after_payment(
    db: Session,
    user: User,
    order: Order,
) -> NotificationEvent:
    order.status = "processing"
    order.progress = 0.18
    order.tracking_eta = tracking_eta_after_payment(
        order.delivery_method,
        get_delivery_config(db),
    )
    order.payment_status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.cancelled_at = None
    order.cancellation_reason = None

    _decrement_order_inventory(db, order)

    existing_redemption = db.scalar(
        select(VoucherRedemption.id).where(VoucherRedemption.order_id == order.id)
    )
    if order.voucher_id and not existing_redemption:
        db.add(
            VoucherRedemption(
                voucher_id=order.voucher_id,
                user_id=user.id,
                order_id=order.id,
                voucher_code=order.voucher_code or "",
                discount_amount=order.discount_amount,
            )
        )

    if order.source == "cart":
        cart_items = list(db.scalars(select(CartItem).where(CartItem.user_id == user.id)).all())
        for cart_item in cart_items:
            db.delete(cart_item)

    from app.controllers.behavior_controller import record_purchase_events_for_order

    record_purchase_events_for_order(db, user, order)

    preview = order_notification_image(order)
    event = create_notification_event(
        db,
        user,
        kind="order_placed",
        title="Order placed successfully",
        body=f"Order #{order.order_number} is now being prepared for delivery.",
        icon="bag-handle-outline",
        accent="neutral",
        action_label="Track order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )

    _maybe_send_order_payment_confirmation_sms(user, order)
    return event


def create_order(db: Session, user: User, payload: OrderCreate) -> Order:
    if payload.payment_type != "cash_on_delivery":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online orders must go through the payment checkout flow.",
        )

    order = prepare_order_for_checkout(
        db,
        user,
        payload,
        payment_provider="cash_on_delivery",
    )
    placed_event = activate_order_after_payment(db, user, order)
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
        notification_event=placed_event,
    )
    db.commit()
    db.refresh(created_order)
    _broadcast_order_realtime(db, created_order)
    _dispatch_vendor_new_order_alerts(db, created_order)
    db.commit()

    record_user_event(
        db,
        user_id=str(user.id),
        event_type=ORDER_CREATED,
        action="commerce.order_created",
        entity_type="order",
        entity_id=str(created_order.id),
        metadata={
            "order_number": created_order.order_number,
            "total_amount": created_order.total_amount,
            "payment_type": created_order.payment_type,
        },
    )

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
        image_url=order_item.image_url,
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

    if order.status not in {"pending_payment", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending or processing orders can be cancelled.",
        )

    if order.payment_status == "paid" and order.status != "pending_payment":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paid orders can no longer be cancelled directly from this screen.",
        )

    order.status = "cancelled"
    order.payment_status = (
        "cancelled" if order.payment_status == "pending" else order.payment_status
    )
    order.progress = 0
    order.tracking_eta = None
    order.cancellation_reason = reason
    order.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    preview = order_notification_image(order)
    cancelled_event = create_notification_event(
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
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )
    _dispatch_order_push(
        user=user,
        title="Order cancelled",
        body=f"Order #{order.order_number} has been cancelled.",
        order=order,
        notification_event=cancelled_event,
    )
    db.commit()
    db.refresh(order)
    _broadcast_order_realtime(db, order)
    return order


def confirm_order_delivery(db: Session, user: User, order_id: str) -> Order:
    order = get_order(db, user, order_id)

    if order.status != "processing" or order.payment_status != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only paid processing orders can be marked as delivered.",
        )

    order.status = "delivered"
    order.progress = 1
    order.tracking_eta = None
    order.cancellation_reason = None
    order.cancelled_at = None
    order.delivered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    preview = order_notification_image(order)
    delivered_event = create_notification_event(
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
        image_key=preview["image_key"],
        image_url=preview["image_url"],
    )
    _dispatch_order_push(
        user=user,
        title="Order delivered",
        body=f"Order #{order.order_number} has arrived successfully.",
        order=order,
        notification_event=delivered_event,
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
