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
    AssistantReferenceContext,
    AssistantSessionResponse,
    AssistantStoreRead,
)
from app.services.assistant_context import build_assistant_user_context
from app.services.assistant_reference import normalize_reference_context, resolve_store_reference


MAX_STORED_MESSAGES = 20


def _conversation_scope_key(conversation: AssistantConversation) -> str | None:
    context = conversation.context_json or {}
    return _scope_key(context)


def _scope_key(context: dict[str, str] | None) -> str | None:
    if not context:
        return None
    if context.get("type") == "product" and context.get("product_id"):
        return f"product:{context['product_id']}"
    store_id = context.get("store_id")
    if store_id:
        return f"store:{store_id}"
    return None


def _default_screen_for(context: dict[str, str] | None) -> str | None:
    if context and context.get("type") == "product":
        return "product"
    if context and context.get("type") == "store":
        return "store"
    return None


def _apply_conversation_metadata(
    conversation: AssistantConversation,
    *,
    screen: str | None,
    context: dict[str, str] | None,
) -> None:
    if screen:
        conversation.screen = screen
    if context:
        conversation.context_json = context


def get_or_create_conversation(
    db: Session,
    user: User,
    *,
    conversation_id: uuid.UUID | None = None,
    screen: str | None = None,
    context: AssistantReferenceContext | dict | None = None,
) -> AssistantConversation:
    resolved_context = resolve_store_reference(db, context) or normalize_reference_context(context)
    target_scope_key = _scope_key(resolved_context)

    if conversation_id is not None:
        existing = db.scalar(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.user_id == user.id,
            )
        )
        if existing is not None:
            # Keep store/product-scoped threads isolated from general chat.
            existing_scope_key = _conversation_scope_key(existing)
            if target_scope_key and existing_scope_key and existing_scope_key != target_scope_key:
                pass
            else:
                _apply_conversation_metadata(
                    existing,
                    screen=screen,
                    context=resolved_context,
                )
                return existing

    recent = list(
        db.scalars(
            select(AssistantConversation)
            .where(AssistantConversation.user_id == user.id)
            .order_by(desc(AssistantConversation.updated_at))
            .limit(25)
        ).all()
    )

    if target_scope_key:
        for candidate in recent:
            if _conversation_scope_key(candidate) == target_scope_key:
                _apply_conversation_metadata(
                    candidate,
                    screen=screen or _default_screen_for(resolved_context),
                    context=resolved_context,
                )
                return candidate
        conversation = AssistantConversation(
            user_id=user.id,
            screen=screen or _default_screen_for(resolved_context),
            context_json=resolved_context,
        )
        db.add(conversation)
        db.flush()
        return conversation

    for candidate in recent:
        if not _conversation_scope_key(candidate):
            _apply_conversation_metadata(
                candidate,
                screen=screen,
                context=None,
            )
            return candidate

    conversation = AssistantConversation(user_id=user.id, screen=screen, context_json=None)
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
    context: AssistantReferenceContext | dict | None = None,
) -> AssistantSessionResponse:
    nudge = build_proactive_nudge(db, user)
    resolved_context = resolve_store_reference(db, context) or normalize_reference_context(context)

    # Store-focused sessions get a store-specific nudge over generic cart prompts.
    if resolved_context and resolved_context.get("store_name"):
        store_name = resolved_context["store_name"]
        nudge = AssistantNudgeRead(
            message=(
                f"You're shopping {store_name}. Ask about products, deals, delivery, "
                "or how to message the store — I'll keep answers focused here."
            ),
            prompt=f"What should I browse first at {store_name}?",
            kind="store_reference",
        )

    if user is None:
        session_context = (
            AssistantReferenceContext.model_validate(resolved_context)
            if resolved_context
            else None
        )
        return AssistantSessionResponse(
            conversation_id=None,
            messages=[],
            nudge=nudge,
            context=session_context,
        )

    conversation = get_or_create_conversation(
        db,
        user,
        conversation_id=conversation_id,
        screen=screen,
        context=resolved_context,
    )
    db.flush()

    stored_context = conversation.context_json or resolved_context
    session_context = (
        AssistantReferenceContext.model_validate(stored_context)
        if stored_context
        else None
    )

    return AssistantSessionResponse(
        conversation_id=str(conversation.id),
        messages=load_conversation_messages(db, conversation.id),
        nudge=nudge,
        context=session_context,
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
