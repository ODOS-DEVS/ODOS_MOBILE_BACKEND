from __future__ import annotations

import logging

import requests

from app.models import NotificationEvent, Order, User

logger = logging.getLogger(__name__)

EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"
VENDOR_ORDER_SOUND = "vendor_order.wav"


def customer_order_status_push_copy(
    *,
    order_number: str,
    vendor_status: str,
    tracking_eta: str | None = None,
) -> tuple[str, str]:
    copies = {
        "pending": (
            "Order received",
            f"Order #{order_number} is waiting for the store to confirm.",
        ),
        "confirmed": (
            "Order confirmed",
            f"Order #{order_number} was confirmed by the store.",
        ),
        "processing": (
            "Order being prepared",
            f"Order #{order_number} is being prepared.",
        ),
        "ready": (
            "Order ready",
            f"Order #{order_number} is ready.",
        ),
        "out_for_delivery": (
            "Out for delivery",
            f"Order #{order_number} is on the way to you.",
        ),
        "delivered": (
            "Order delivered",
            f"Order #{order_number} has been delivered.",
        ),
        "cancelled": (
            "Order cancelled",
            f"Order #{order_number} was cancelled.",
        ),
    }
    title, body = copies.get(
        vendor_status,
        (
            "Order update",
            f"Order #{order_number} is now {vendor_status.replace('_', ' ')}.",
        ),
    )
    if tracking_eta and vendor_status not in {"delivered", "cancelled"}:
        body = f"{body.rstrip('.')} · {tracking_eta}"
    return title, body


def dispatch_customer_order_push(
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
                    "vendorStatus": order.vendor_status,
                },
            ),
        )
    except Exception:
        logger.exception("Failed to send order push for %s", order.id)


def dispatch_customer_return_push(
    *,
    user: User,
    title: str,
    body: str,
    order_id,
    notification_event: NotificationEvent | None = None,
) -> None:
    try:
        send_expo_push_notification(
            user=user,
            title=title,
            body=body,
            data=build_push_data(
                push_type="return_update",
                route_type="order",
                route_target_id=str(order_id),
                notification_event=notification_event,
                extra={"orderId": str(order_id)},
            ),
        )
    except Exception:
        logger.exception("Failed to send return push for order %s", order_id)


def build_push_data(
    *,
    push_type: str,
    route_type: str | None = None,
    route_target_id: str | None = None,
    notification_event: NotificationEvent | None = None,
    extra: dict | None = None,
) -> dict:
    data: dict = {"type": push_type}

    if route_type:
        data["routeType"] = route_type
    if route_target_id:
        data["routeTargetId"] = route_target_id
    if notification_event is not None:
        data["notificationId"] = str(notification_event.id)
        data["kind"] = notification_event.kind
    if extra:
        data.update(extra)

    return data


def can_receive_vendor_order_alerts(user: User) -> bool:
    return bool(
        user.expo_push_token
        and user.allow_notifications
        and user.vendor_order_notifications
    )


def can_receive_vendor_chat_alerts(user: User) -> bool:
    return bool(
        user.expo_push_token
        and user.allow_notifications
        and user.store_notifications
    )


def send_expo_push_notification(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
    channel_id: str = "default",
    sound: str = "default",
) -> None:
    if not user.expo_push_token or not user.allow_notifications:
        return

    payload = {
        "to": user.expo_push_token,
        "title": title,
        "body": body,
        "data": data or {},
        "sound": sound,
        "priority": "high",
        "channelId": channel_id,
    }

    response = requests.post(
        EXPO_PUSH_ENDPOINT,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if response.status_code >= 400:
        logger.warning(
            "Expo push send failed for user %s with status %s: %s",
            user.id,
            response.status_code,
            response.text,
        )


def can_receive_customer_chat_alerts(user: User) -> bool:
    return bool(
        user.expo_push_token
        and user.allow_notifications
        and user.store_notifications
    )


def send_vendor_order_push(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not can_receive_vendor_order_alerts(user):
        return

    send_expo_push_notification(
        user=user,
        title=title,
        body=body,
        data=data,
        channel_id="vendor-orders",
        sound=VENDOR_ORDER_SOUND,
    )


def send_vendor_chat_push(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not can_receive_vendor_chat_alerts(user):
        return

    send_expo_push_notification(
        user=user,
        title=title,
        body=body,
        data=data,
        channel_id="vendor-chats",
        sound="default",
    )


def send_customer_chat_push(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not can_receive_customer_chat_alerts(user):
        return

    send_expo_push_notification(
        user=user,
        title=title,
        body=body,
        data=data,
        channel_id="customer-chats",
        sound="default",
    )
