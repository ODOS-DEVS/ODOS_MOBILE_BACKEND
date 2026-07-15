from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import NotificationEvent, NotificationRead, Order, User
from app.schemas.notification import NotificationEventRead, NotificationPageRead
from app.services.realtime_service import realtime_manager

DEFAULT_NOTIFICATION_PAGE_SIZE = 25
MAX_NOTIFICATION_PAGE_SIZE = 50


def normalize_notification_page_params(limit: int | None, offset: int | None) -> tuple[int, int]:
    resolved_limit = DEFAULT_NOTIFICATION_PAGE_SIZE if limit is None else limit
    resolved_offset = 0 if offset is None else offset
    return max(1, min(resolved_limit, MAX_NOTIFICATION_PAGE_SIZE)), max(0, resolved_offset)


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


def _notification_events_query(user: User):
    return (
        select(NotificationEvent)
        .where(NotificationEvent.user_id == user.id)
        .order_by(NotificationEvent.created_at.desc())
    )


def _count_notification_events(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(NotificationEvent)
            .where(NotificationEvent.user_id == user.id)
        )
        or 0
    )


def _normalize_notification_key(value: object) -> str:
    return str(value).strip().lower()


def _count_unread_notifications(db: Session, user: User) -> int:
    notification_ids = {
        _normalize_notification_key(notification_id)
        for notification_id in db.scalars(
            select(NotificationEvent.id).where(NotificationEvent.user_id == user.id)
        ).all()
    }
    if not notification_ids:
        return 0

    read_keys = {
        _normalize_notification_key(key)
        for key in list_notification_read_keys(db, user)
    }
    read_notification_count = len(notification_ids.intersection(read_keys))
    return len(notification_ids) - read_notification_count


def _bootstrap_notification_events(db: Session, user: User) -> None:
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


def list_notification_events_page(
    db: Session,
    user: User,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> NotificationPageRead:
    resolved_limit, resolved_offset = normalize_notification_page_params(limit, offset)

    if resolved_offset == 0 and _count_notification_events(db, user) == 0:
        _bootstrap_notification_events(db, user)

    rows = list(
        db.scalars(
            _notification_events_query(user).offset(resolved_offset).limit(resolved_limit + 1)
        ).all()
    )
    has_more = len(rows) > resolved_limit
    items = rows[:resolved_limit]

    return NotificationPageRead(
        items=[NotificationEventRead.model_validate(item) for item in items],
        has_more=has_more,
        total_count=_count_notification_events(db, user),
        unread_count=_count_unread_notifications(db, user),
    )


def list_notification_events(db: Session, user: User) -> list[NotificationEvent]:
    page = list_notification_events_page(db, user, limit=MAX_NOTIFICATION_PAGE_SIZE, offset=0)
    if page.has_more:
        all_items = list(page.items)
        offset = len(all_items)
        while page.has_more:
            page = list_notification_events_page(
                db,
                user,
                limit=MAX_NOTIFICATION_PAGE_SIZE,
                offset=offset,
            )
            all_items.extend(page.items)
            offset += len(page.items)
        return all_items

    return list(page.items)


def list_notification_read_keys(db: Session, user: User) -> list[str]:
    return [
        _normalize_notification_key(key)
        for key in db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == user.id)
        ).all()
    ]


def build_notification_read_state(db: Session, user: User) -> dict[str, object]:
    return {
        "read_keys": list_notification_read_keys(db, user),
        "unread_count": _count_unread_notifications(db, user),
    }


def mark_notification_keys_read(
    db: Session,
    user: User,
    keys: list[str],
    *,
    mark_all: bool = False,
) -> list[str]:
    if mark_all:
        notification_ids = list(
            db.scalars(
                select(NotificationEvent.id).where(NotificationEvent.user_id == user.id)
            ).all()
        )
        unique_keys = [
            _normalize_notification_key(notification_id) for notification_id in notification_ids
        ]
    else:
        unique_keys = [
            _normalize_notification_key(key) for key in dict.fromkeys(keys) if str(key).strip()
        ]
    if not unique_keys:
        return list_notification_read_keys(db, user)

    existing = {
        _normalize_notification_key(key)
        for key in db.scalars(
            select(NotificationRead.notification_key).where(
                NotificationRead.user_id == user.id,
            )
        ).all()
    }

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


def unregister_expo_push_token(db: Session, user: User) -> User:
    user.expo_push_token = None
    db.commit()
    db.refresh(user)
    return user
