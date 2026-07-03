from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.event_types import (
    CART_UPDATED,
    PRODUCT_VIEW,
    SEARCH_QUERY,
)
from app.models import Order, OrderItem, User, UserBehaviorEvent
from app.services.event_log_service import record_user_event
from app.schemas.behavior import (
    SUPPORTED_BEHAVIOR_EVENT_TYPES,
    BehaviorEventBatchCreate,
    BehaviorEventBatchRead,
)


def _normalize_search_query(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.strip().split())
    if len(cleaned) < 2:
        return None
    return cleaned[:255]


def record_behavior_event_batch(
    db: Session,
    user: User,
    payload: BehaviorEventBatchCreate,
) -> BehaviorEventBatchRead:
    if not user.analytics_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Behavior tracking is disabled for this account.",
        )

    accepted = 0
    now = datetime.now(timezone.utc)

    behavior_to_audit = {
        "product_view": (PRODUCT_VIEW, "commerce.product_view"),
        "search_query": (SEARCH_QUERY, "commerce.search_query"),
        "add_to_cart": (CART_UPDATED, "commerce.cart_add"),
        "remove_from_cart": (CART_UPDATED, "commerce.cart_remove"),
    }

    for event in payload.events:
        if event.event_type not in SUPPORTED_BEHAVIOR_EVENT_TYPES:
            continue
        if event.event_type == "purchase":
            continue

        db.add(
            UserBehaviorEvent(
                user_id=user.id,
                session_id=payload.session_id,
                event_type=event.event_type,
                product_id=event.product_id,
                store_id=event.store_id,
                category=event.category,
                search_query=_normalize_search_query(event.search_query),
                source_screen=event.source_screen,
                metadata_json=event.metadata,
                created_at=event.occurred_at or now,
            )
        )
        accepted += 1

        audit_mapping = behavior_to_audit.get(event.event_type)
        if audit_mapping:
            audit_type, audit_action = audit_mapping
            record_user_event(
                db,
                user_id=str(user.id),
                event_type=audit_type,
                action=audit_action,
                entity_type="product" if event.product_id else None,
                entity_id=event.product_id,
                metadata={
                    "session_id": payload.session_id,
                    "search_query": _normalize_search_query(event.search_query),
                    "source_screen": event.source_screen,
                    **(event.metadata or {}),
                },
                commit=False,
            )

    if accepted:
        db.commit()

    return BehaviorEventBatchRead(
        accepted=accepted,
        session_id=payload.session_id,
    )


def record_purchase_events_for_order(db: Session, user: User, order: Order) -> None:
    if not user.analytics_enabled:
        return

    items = list(order.items) if order.items else []
    if not items:
        items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all())

    now = datetime.now(timezone.utc)
    for item in items:
        db.add(
            UserBehaviorEvent(
                user_id=user.id,
                session_id=None,
                event_type="purchase",
                product_id=item.product_id,
                store_id=item.store_id,
                category=item.category,
                source_screen="checkout",
                metadata_json={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "line_total": float(item.line_total),
                },
                created_at=now,
            )
        )
