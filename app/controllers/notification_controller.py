from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NotificationEvent, NotificationRead, Order, User
from app.schemas.notification import NotificationEventRead
from app.services.realtime_service import realtime_manager


def order_notification_image(order: Order) -> dict[str, str | None]:
    if not order.items:
        return {"image_key": None, "image_url": None}

    item = order.items[0]
    return {
        "image_key": item.image_key,
        "image_url": item.image_url,
    }


def create_notification_event(
    db: Session,
    user: User,
    *,
    kind: str,
    title: str,
    body: str,
    icon: str,
    accent: str = "neutral",
    action_label: str | None = None,
    route_type: str | None = None,
    route_target_id: str | None = None,
    image_key: str | None = None,
    image_url: str | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        user_id=user.id,
        kind=kind,
        title=title,
        body=body,
        icon=icon,
        accent=accent,
        action_label=action_label,
        route_type=route_type,
        route_target_id=route_target_id,
        image_key=image_key,
        image_url=image_url,
    )
    db.add(event)
    db.flush()
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "notification.created",
        NotificationEventRead.model_validate(event).model_dump(mode="json"),
    )
    return event


def list_notification_events(db: Session, user: User) -> list[NotificationEvent]:
    events = list(
        db.scalars(
            select(NotificationEvent)
            .where(NotificationEvent.user_id == user.id)
            .order_by(NotificationEvent.created_at.desc())
        ).all()
    )
    if events:
        return events

    create_notification_event(
        db,
        user,
        kind="account_ready",
        title="Your account is ready",
        body="You can browse, place orders, and manage everything from your profile.",
        icon="person-outline",
        accent="neutral",
        action_label="Open profile",
        route_type="profile",
        route_target_id=str(user.id),
    )

    if user.is_verified:
        create_notification_event(
            db,
            user,
            kind="email_verified",
            title="Email verified successfully",
            body="Your account is fully verified and ready for secure shopping.",
            icon="mail-outline",
            accent="success",
            action_label="View profile",
            route_type="profile",
            route_target_id=str(user.id),
        )

    recent_orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
            .limit(10)
        ).all()
    )
    for order in recent_orders:
        preview = order_notification_image(order)
        create_notification_event(
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
        if order.status == "delivered":
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
                image_key=preview["image_key"],
                image_url=preview["image_url"],
            )
        elif order.status == "cancelled":
            create_notification_event(
                db,
                user,
                kind="order_cancelled",
                title="Order cancelled",
                body=order.cancellation_reason or f"Order #{order.order_number} was cancelled.",
                icon="close-circle-outline",
                accent="warning",
                action_label="Review order",
                route_type="order",
                route_target_id=str(order.id),
                image_key=preview["image_key"],
                image_url=preview["image_url"],
            )

    db.commit()
    return list(
        db.scalars(
            select(NotificationEvent)
            .where(NotificationEvent.user_id == user.id)
            .order_by(NotificationEvent.created_at.desc())
        ).all()
    )


def list_notification_read_keys(db: Session, user: User) -> list[str]:
    return list(
        db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == user.id)
        ).all()
    )


def mark_notification_keys_read(db: Session, user: User, keys: list[str]) -> list[str]:
    unique_keys = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    if not unique_keys:
        return list_notification_read_keys(db, user)

    existing = set(
        db.scalars(
            select(NotificationRead.notification_key).where(
                NotificationRead.user_id == user.id,
                NotificationRead.notification_key.in_(unique_keys),
            )
        ).all()
    )

    for key in unique_keys:
        if key in existing:
            continue
        db.add(
            NotificationRead(
                user_id=user.id,
                notification_key=key,
                read_at=datetime.now(UTC),
            )
        )

    db.commit()
    return list_notification_read_keys(db, user)


def register_expo_push_token(db: Session, user: User, expo_push_token: str) -> User:
    user.expo_push_token = expo_push_token.strip()
    db.commit()
    db.refresh(user)
    return user
