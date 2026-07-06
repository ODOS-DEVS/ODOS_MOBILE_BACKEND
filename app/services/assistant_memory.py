from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from app.models import CartItem, User, Voucher, VoucherAssignment
from app.models.assistant import (
    AssistantConversation,
    AssistantMessage,
    AssistantMessageFeedback,
)
from app.models.order import Order
from app.schemas.assistant import (
    AssistantActionRead,
    AssistantFeedbackRequest,
    AssistantMessageRead,
    AssistantNudgeRead,
    AssistantProductRead,
    AssistantSessionResponse,
    AssistantStoreRead,
)
from app.services.assistant_context import build_assistant_user_context


MAX_STORED_MESSAGES = 20


def get_or_create_conversation(
    db: Session,
    user: User,
    *,
    conversation_id: uuid.UUID | None = None,
    screen: str | None = None,
) -> AssistantConversation:
    if conversation_id is not None:
        existing = db.scalar(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.user_id == user.id,
            )
        )
        if existing is not None:
            if screen and existing.screen != screen:
                existing.screen = screen
            return existing

    latest = db.scalar(
        select(AssistantConversation)
        .where(AssistantConversation.user_id == user.id)
        .order_by(desc(AssistantConversation.updated_at))
        .limit(1)
    )
    if latest is not None:
        if screen:
            latest.screen = screen
        return latest

    conversation = AssistantConversation(user_id=user.id, screen=screen)
    db.add(conversation)
    db.flush()
    return conversation


def load_conversation_messages(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    limit: int = MAX_STORED_MESSAGES,
) -> list[AssistantMessageRead]:
    rows = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    payload: list[AssistantMessageRead] = []
    for row in rows:
        metadata = row.metadata_json or {}
        raw_actions = metadata.get("suggested_actions") or []
        raw_products = metadata.get("products") or []
        raw_stores = metadata.get("stores") or []
        payload.append(
            AssistantMessageRead(
                id=str(row.id),
                role=row.role,
                content=row.content,
                suggested_actions=[
                    AssistantActionRead.model_validate(item)
                    for item in raw_actions
                    if isinstance(item, dict)
                ]
                or None,
                products=[
                    AssistantProductRead.model_validate(item)
                    for item in raw_products
                    if isinstance(item, dict)
                ]
                or None,
                stores=[
                    AssistantStoreRead.model_validate(item)
                    for item in raw_stores
                    if isinstance(item, dict)
                ]
                or None,
                escalated_to_support=bool(metadata.get("escalated_to_support")),
                feedback_rating=metadata.get("feedback_rating"),
                created_at=row.created_at,
            )
        )
    return payload


def append_conversation_message(
    db: Session,
    conversation: AssistantConversation,
    *,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> AssistantMessage:
    message = AssistantMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        metadata_json=metadata,
    )
    db.add(message)
    conversation.updated_at = datetime.now(timezone.utc)
    db.flush()
    _trim_conversation_messages(db, conversation.id)
    return message


def _trim_conversation_messages(db: Session, conversation_id: uuid.UUID) -> None:
    rows = list(
        db.scalars(
            select(AssistantMessage.id)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at.desc())
            .offset(MAX_STORED_MESSAGES)
        ).all()
    )
    if not rows:
        return
    db.execute(delete(AssistantMessage).where(AssistantMessage.id.in_(rows)))


def history_from_conversation(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    rows = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]


def save_message_feedback(
    db: Session,
    user: User,
    payload: AssistantFeedbackRequest,
) -> int:
    message = db.scalar(
        select(AssistantMessage)
        .join(AssistantConversation, AssistantConversation.id == AssistantMessage.conversation_id)
        .where(
            AssistantMessage.id == payload.message_id,
            AssistantConversation.user_id == user.id,
        )
    )
    if message is None:
        raise ValueError("Assistant message not found.")

    existing = db.scalar(
        select(AssistantMessageFeedback).where(
            AssistantMessageFeedback.message_id == payload.message_id,
            AssistantMessageFeedback.user_id == user.id,
        )
    )
    if existing is not None:
        existing.rating = payload.rating
        existing.comment = payload.comment
    else:
        db.add(
            AssistantMessageFeedback(
                message_id=payload.message_id,
                user_id=user.id,
                rating=payload.rating,
                comment=payload.comment,
            )
        )

    metadata = dict(message.metadata_json or {})
    metadata["feedback_rating"] = payload.rating
    message.metadata_json = metadata
    db.flush()
    return payload.rating


def build_assistant_session(
    db: Session,
    user: User | None,
    *,
    conversation_id: uuid.UUID | None = None,
    screen: str | None = None,
) -> AssistantSessionResponse:
    nudge = build_proactive_nudge(db, user)
    if user is None:
        return AssistantSessionResponse(
            conversation_id=None,
            messages=[],
            nudge=nudge,
        )

    conversation = get_or_create_conversation(
        db,
        user,
        conversation_id=conversation_id,
        screen=screen,
    )
    db.flush()

    return AssistantSessionResponse(
        conversation_id=str(conversation.id),
        messages=load_conversation_messages(db, conversation.id),
        nudge=nudge,
    )


def build_proactive_nudge(db: Session, user: User | None) -> AssistantNudgeRead | None:
    if user is None:
        return None

    _, snapshot = build_assistant_user_context(db, user)
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)

    cart_item = db.scalar(
        select(CartItem)
        .where(CartItem.user_id == user.id)
        .order_by(desc(CartItem.updated_at))
        .limit(1)
    )
    if cart_item is not None:
        return AssistantNudgeRead(
            message=f"Still thinking about {cart_item.title}? I can help you checkout or find a voucher.",
            prompt=f"Is my cart ready for checkout?",
            kind="cart",
        )

    expiring_voucher = db.scalar(
        select(Voucher)
        .join(VoucherAssignment, VoucherAssignment.voucher_id == Voucher.id)
        .where(
            VoucherAssignment.user_id == user.id,
            Voucher.is_active.is_(True),
            Voucher.approval_status == "approved",
            Voucher.ends_at.is_not(None),
            Voucher.ends_at <= tomorrow,
            Voucher.ends_at > now,
        )
        .order_by(Voucher.ends_at.asc())
        .limit(1)
    )
    if expiring_voucher is not None:
        return AssistantNudgeRead(
            message=(
                f"Your voucher {expiring_voucher.code} expires soon — want help using it at checkout?"
            ),
            prompt="How do I use my expiring voucher at checkout?",
            kind="voucher",
        )

    delayed_order = db.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.status.in_(("processing", "confirmed", "packed", "shipped")),
        )
        .order_by(desc(Order.created_at))
        .limit(1)
    )
    if delayed_order is not None and snapshot and snapshot.latest_order_id:
        return AssistantNudgeRead(
            message=(
                f"Want an update on order #{snapshot.latest_order_number}? "
                "I can explain the status or connect you with the store."
            ),
            prompt="Where is my latest order?",
            kind="order",
        )

    return None
