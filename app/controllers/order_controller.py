import secrets

from datetime import datetime, timezone
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.controllers.voucher_controller import build_voucher_quote
from app.models import CartItem, Order, OrderItem, User, VoucherRedemption
from app.schemas.order import OrderCreate
from app.services.push_service import send_expo_push_notification

logger = logging.getLogger(__name__)


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
        .options(selectinload(Order.items))
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

    return created_order


def list_orders(db: Session, user: User) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.user_id == user.id)
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
        ).all()
    )


def get_order(db: Session, user: User, order_id: str) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id, Order.user_id == user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found.",
        )

    return order


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
