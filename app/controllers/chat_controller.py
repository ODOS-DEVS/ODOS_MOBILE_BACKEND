import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ChatMessage,
    ChatThread,
    SupportChatStatus,
    ChatThreadType,
    Product,
    Store,
    User,
    UserRole,
    VendorStatus,
)
from app.schemas.chat import (
    ChatCounterpartRead,
    ChatMessageCreate,
    ChatMessageRead,
    ChatProductSummaryRead,
    ChatStoreSummaryRead,
    SupportChatStatusUpdate,
    SupportChatThreadEnsurePayload,
    ChatThreadEnsurePayload,
    ChatThreadRead,
)
from app.controllers.notification_controller import create_notification_event
from app.services.push_service import build_push_data, send_vendor_chat_push
from app.services.realtime_service import realtime_manager


def require_vendor_access(user: User) -> None:
    if user.role == UserRole.ADMIN:
        return

    if user.vendor_status == VendorStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vendor access is currently suspended for this account.",
        )

    if user.vendor_status != VendorStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your vendor access is not approved yet.",
        )


def _load_thread(db: Session, thread_id: str | object) -> ChatThread | None:
    return db.scalar(
        select(ChatThread)
        .options(
            selectinload(ChatThread.store),
            selectinload(ChatThread.customer_user),
            selectinload(ChatThread.vendor_user),
            selectinload(ChatThread.assigned_admin_user),
        )
        .where(ChatThread.id == thread_id)
    )


def _serialize_thread(
    thread: ChatThread,
    current_user: User,
    *,
    unread_count: int = 0,
) -> ChatThreadRead:
    if thread.thread_type == ChatThreadType.SUPPORT:
        if current_user.id == thread.customer_user_id:
            counterpart = ChatCounterpartRead(
                user_id=thread.vendor_user_id,
                name="ODOS Support",
                avatar_url=thread.vendor_user.avatar_url if thread.vendor_user else thread.store.image_url,
                role="admin",
            )
        else:
            requester_role = "vendor" if "vendor" in thread.customer_user.roles else "customer"
            counterpart = ChatCounterpartRead(
                user_id=thread.customer_user_id,
                name=thread.customer_user.full_name,
                avatar_url=thread.customer_user.avatar_url,
                role=requester_role,
            )
    elif current_user.id == thread.customer_user_id:
        counterpart = ChatCounterpartRead(
            user_id=thread.vendor_user_id,
            name=thread.store.title if thread.store else thread.vendor_user.full_name,
            avatar_url=thread.store.image_url if thread.store else thread.vendor_user.avatar_url,
            role="vendor",
        )
    else:
        counterpart = ChatCounterpartRead(
            user_id=thread.customer_user_id,
            name=thread.customer_user.full_name,
            avatar_url=thread.customer_user.avatar_url,
            role="customer",
        )

    product = None
    if thread.product_id or thread.product_title or thread.product_image_url:
        product = ChatProductSummaryRead(
            id=thread.product_id,
            title=thread.product_title,
            image_url=thread.product_image_url,
        )

    return ChatThreadRead(
        id=thread.id,
        customer_user_id=thread.customer_user_id,
        vendor_user_id=thread.vendor_user_id,
        thread_type=thread.thread_type.value,
        store=ChatStoreSummaryRead(
            id=thread.store.id,
            title=thread.store.title,
            image_key=thread.store.image_key,
            image_url=thread.store.image_url,
        ),
        counterpart=counterpart,
        subject=thread.subject,
        product=product,
        support_status=thread.support_status.value if thread.support_status else None,
        assigned_admin_user_id=thread.assigned_admin_user_id,
        assigned_admin_name=thread.assigned_admin_user.full_name
        if thread.assigned_admin_user
        else None,
        assigned_admin_at=thread.assigned_admin_at,
        resolved_at=thread.resolved_at,
        last_message_text=thread.last_message_text,
        last_message_at=thread.last_message_at,
        unread_count=unread_count,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _serialize_message(message: ChatMessage, thread: ChatThread) -> ChatMessageRead:
    if thread.thread_type == ChatThreadType.SUPPORT:
        if message.sender_user_id == thread.vendor_user_id:
            sender_role = "admin"
        else:
            sender_role = "vendor" if "vendor" in thread.customer_user.roles else "customer"
    else:
        sender_role = "customer" if message.sender_user_id == thread.customer_user_id else "vendor"
    return ChatMessageRead(
        id=message.id,
        thread_id=message.thread_id,
        sender_user_id=message.sender_user_id,
        recipient_user_id=message.recipient_user_id,
        sender_role=sender_role,
        body=message.body,
        is_read=message.is_read,
        read_at=message.read_at,
        created_at=message.created_at,
    )


def _get_store_or_404(db: Session, store_id: str) -> Store:
    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store was not found.",
        )
    return store


def _get_platform_store_or_404(db: Session) -> Store:
    store = db.scalar(select(Store).where(Store.slug == "odos-official"))
    if not store:
        store = Store(
            id=f"store-{uuid.uuid4().hex[:10]}",
            slug="odos-official",
            title="ODOS Support",
            category="Support",
            market_id=None,
            market_slug=None,
            image_key="bag",
            image_url=None,
            rating=None,
            address="ODOS Marketplace",
            phone=None,
            email="support@odos.app",
            city="Accra",
            region="Greater Accra",
            distance_km=None,
            travel_minutes=None,
            description="Platform-managed support conversations for ODOS users and vendors.",
            image_banner_key=None,
            image_banner_url=None,
            status="active",
            vendor_user_id=None,
            sort_order=0,
            is_active=True,
        )
        db.add(store)
        db.flush()
    return store


def _get_product_for_store(
    db: Session,
    store: Store,
    product_id: str | None,
) -> Product | None:
    if not product_id:
        return None

    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That product was not found.",
        )

    if product.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That product does not belong to the selected store.",
        )

    return product


def _authorize_thread_participation(thread: ChatThread, user: User) -> None:
    if user.id not in {thread.customer_user_id, thread.vendor_user_id} and user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this chat thread.",
        )


def _serialize_read_event(
    *,
    thread_id: uuid.UUID,
    reader_user_id: uuid.UUID,
    message_ids: list[uuid.UUID],
    read_at: datetime,
) -> dict[str, object]:
    return {
        "thread_id": str(thread_id),
        "reader_user_id": str(reader_user_id),
        "message_ids": [str(message_id) for message_id in message_ids],
        "read_at": read_at.isoformat(),
    }


def _publish_thread_updates(db: Session, thread: ChatThread) -> None:
    refreshed_thread = _load_thread(db, thread.id)
    if not refreshed_thread or not refreshed_thread.customer_user or not refreshed_thread.vendor_user:
        return

    customer_unread_count = int(
        db.scalar(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.thread_id == refreshed_thread.id,
                ChatMessage.recipient_user_id == refreshed_thread.customer_user_id,
                ChatMessage.is_read.is_(False),
            )
        )
        or 0
    )
    vendor_unread_count = int(
        db.scalar(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.thread_id == refreshed_thread.id,
                ChatMessage.recipient_user_id == refreshed_thread.vendor_user_id,
                ChatMessage.is_read.is_(False),
            )
        )
        or 0
    )

    realtime_manager.publish_user_event_sync(
        str(refreshed_thread.customer_user_id),
        "chat.thread.updated",
        _serialize_thread(
            refreshed_thread,
            refreshed_thread.customer_user,
            unread_count=customer_unread_count,
        ).model_dump(mode="json"),
    )
    realtime_manager.publish_user_event_sync(
        str(refreshed_thread.vendor_user_id),
        "chat.thread.updated",
        _serialize_thread(
            refreshed_thread,
            refreshed_thread.vendor_user,
            unread_count=vendor_unread_count,
        ).model_dump(mode="json"),
    )


def _resolve_support_admin(db: Session) -> User:
    admin_user = db.scalar(
        select(User)
        .where(
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
        )
        .order_by(User.updated_at.desc(), User.created_at.asc())
        .limit(1)
    )
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support chat is not available yet.",
        )
    return admin_user


def list_chat_threads(
    db: Session,
    user: User,
    *,
    scope: Literal["customer", "vendor", "support"] = "customer",
) -> list[ChatThreadRead]:
    if scope == "vendor":
        require_vendor_access(user)
        query = select(ChatThread).where(
            ChatThread.vendor_user_id == user.id,
            ChatThread.thread_type == ChatThreadType.STORE,
        )
    elif scope == "support":
        if user.role == UserRole.ADMIN:
            query = select(ChatThread).where(
                ChatThread.vendor_user_id == user.id,
                ChatThread.thread_type == ChatThreadType.SUPPORT,
            )
        else:
            query = select(ChatThread).where(
                ChatThread.customer_user_id == user.id,
                ChatThread.thread_type == ChatThreadType.SUPPORT,
            )
    else:
        query = select(ChatThread).where(
            ChatThread.customer_user_id == user.id,
            ChatThread.thread_type == ChatThreadType.STORE,
        )

    threads = list(
        db.scalars(
            query.options(
                selectinload(ChatThread.store),
                selectinload(ChatThread.customer_user),
                selectinload(ChatThread.vendor_user),
            ).order_by(ChatThread.last_message_at.desc().nullslast(), ChatThread.updated_at.desc())
        ).all()
    )

    if not threads:
        return []

    unread_counts = {
        thread_id: int(count)
        for thread_id, count in db.execute(
            select(ChatMessage.thread_id, func.count(ChatMessage.id))
            .where(
                ChatMessage.recipient_user_id == user.id,
                ChatMessage.is_read.is_(False),
                ChatMessage.thread_id.in_([thread.id for thread in threads]),
            )
            .group_by(ChatMessage.thread_id)
        ).all()
    }

    return [
        _serialize_thread(thread, user, unread_count=unread_counts.get(thread.id, 0))
        for thread in threads
    ]


def ensure_store_chat_thread(
    db: Session,
    user: User,
    store_id: str,
    payload: ChatThreadEnsurePayload,
) -> ChatThreadRead:
    store = _get_store_or_404(db, store_id)
    if not store.vendor_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This store is not ready for chat yet.",
        )

    if store.vendor_user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot open a shopper chat with your own store.",
        )

    product = _get_product_for_store(db, store, payload.product_id)

    thread = db.scalar(
        select(ChatThread).where(
            ChatThread.customer_user_id == user.id,
            ChatThread.store_id == store.id,
            ChatThread.thread_type == ChatThreadType.STORE,
        )
    )

    if thread is None:
        thread = ChatThread(
            customer_user_id=user.id,
            vendor_user_id=store.vendor_user_id,
            store_id=store.id,
            thread_type=ChatThreadType.STORE,
        )
        db.add(thread)

    thread.vendor_user_id = store.vendor_user_id
    if product is not None:
        thread.product_id = product.id
        thread.product_title = product.title
        thread.product_image_url = product.image_url
    elif payload.product_title or payload.product_image_url:
        thread.product_id = payload.product_id
        thread.product_title = payload.product_title
        thread.product_image_url = payload.product_image_url

    db.commit()

    created_thread = _load_thread(db, thread.id)
    if not created_thread:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't prepare the chat thread right now.",
        )

    return _serialize_thread(created_thread, user, unread_count=0)


def ensure_support_chat_thread(
    db: Session,
    user: User,
    payload: SupportChatThreadEnsurePayload,
) -> ChatThreadRead:
    if user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin accounts cannot open support chat as requesters.",
        )

    platform_store = _get_platform_store_or_404(db)
    admin_user = _resolve_support_admin(db)

    thread = db.scalar(
        select(ChatThread).where(
            ChatThread.customer_user_id == user.id,
            ChatThread.store_id == platform_store.id,
            ChatThread.thread_type == ChatThreadType.SUPPORT,
        )
    )

    if thread is None:
        thread = ChatThread(
            customer_user_id=user.id,
            vendor_user_id=admin_user.id,
            store_id=platform_store.id,
            thread_type=ChatThreadType.SUPPORT,
            subject=payload.subject,
            support_status=SupportChatStatus.WAITING_ON_ADMIN,
        )
        db.add(thread)
    else:
        thread.vendor_user_id = admin_user.id
        if payload.subject and not thread.subject:
            thread.subject = payload.subject
        if thread.support_status is None:
            thread.support_status = SupportChatStatus.WAITING_ON_ADMIN

    db.commit()

    created_thread = _load_thread(db, thread.id)
    if not created_thread:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't open support chat right now.",
        )

    return _serialize_thread(created_thread, user, unread_count=0)


def list_chat_messages(db: Session, user: User, thread_id: str) -> list[ChatMessageRead]:
    thread = _load_thread(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That chat thread was not found.",
        )

    _authorize_thread_participation(thread, user)

    if user.id == thread.vendor_user_id and thread.thread_type == ChatThreadType.STORE:
        require_vendor_access(user)

    messages = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.asc())
        ).all()
    )

    now = datetime.now(UTC)
    did_update_reads = False
    newly_read_message_ids: list[uuid.UUID] = []
    for message in messages:
        if message.recipient_user_id == user.id and not message.is_read:
            message.is_read = True
            message.read_at = now
            did_update_reads = True
            newly_read_message_ids.append(message.id)

    if did_update_reads:
        db.commit()
        realtime_manager.publish_user_event_sync(
            str(thread.customer_user_id),
            "chat.messages.read",
            _serialize_read_event(
                thread_id=thread.id,
                reader_user_id=user.id,
                message_ids=newly_read_message_ids,
                read_at=now,
            ),
        )
        realtime_manager.publish_user_event_sync(
            str(thread.vendor_user_id),
            "chat.messages.read",
            _serialize_read_event(
                thread_id=thread.id,
                reader_user_id=user.id,
                message_ids=newly_read_message_ids,
                read_at=now,
            ),
        )
        _publish_thread_updates(db, thread)

    return [_serialize_message(message, thread) for message in messages]


def _notify_vendor_shopper_message(
    db: Session,
    *,
    thread: ChatThread,
    message: ChatMessage,
    customer: User,
) -> None:
    vendor = thread.vendor_user or db.get(User, thread.vendor_user_id)
    if not vendor or thread.thread_type != ChatThreadType.STORE:
        return

    customer_name = (customer.full_name or customer.email or "A shopper").strip()
    preview = (message.body or "").strip()
    if len(preview) > 140:
        preview = f"{preview[:137]}..."

    store_title = thread.store.title if thread.store else "your store"
    notification_event = create_notification_event(
        db,
        vendor,
        kind="vendor_chat_message",
        title=f"Message from {customer_name}",
        body=preview or "New shopper message waiting for your reply.",
        icon="chatbubble-ellipses-outline",
        accent="info",
        action_label="Reply",
        route_type="vendor_chat",
        route_target_id=str(thread.id),
    )
    send_vendor_chat_push(
        user=vendor,
        title=f"{customer_name} · {store_title}",
        body=preview or "New shopper message waiting for your reply.",
        data=build_push_data(
            push_type="vendor_chat_message",
            route_type="vendor_chat",
            route_target_id=str(thread.id),
            notification_event=notification_event,
            extra={
                "threadId": str(thread.id),
                "storeId": str(thread.store_id),
                "storeName": store_title,
                "customerName": customer_name,
            },
        ),
    )
    db.commit()


def post_chat_message(
    db: Session,
    user: User,
    thread_id: str,
    payload: ChatMessageCreate,
) -> ChatMessageRead:
    thread = _load_thread(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That chat thread was not found.",
        )

    _authorize_thread_participation(thread, user)

    if user.id == thread.vendor_user_id and thread.thread_type == ChatThreadType.STORE:
        require_vendor_access(user)

    if thread.thread_type == ChatThreadType.SUPPORT and user.role == UserRole.ADMIN:
        thread.vendor_user_id = user.id
        thread.vendor_user = user
        thread.assigned_admin_user_id = user.id
        if thread.assigned_admin_at is None:
            thread.assigned_admin_at = datetime.now(UTC)

    if user.id == thread.customer_user_id:
        recipient_user_id = thread.vendor_user_id
    else:
        recipient_user_id = thread.customer_user_id

    message = ChatMessage(
        thread_id=thread.id,
        sender_user_id=user.id,
        recipient_user_id=recipient_user_id,
        body=payload.body,
    )
    db.add(message)

    now = datetime.now(UTC)
    thread.last_message_text = payload.body
    thread.last_message_at = now
    if thread.thread_type == ChatThreadType.SUPPORT:
        if user.role == UserRole.ADMIN:
            thread.support_status = SupportChatStatus.WAITING_ON_CUSTOMER
        else:
            thread.support_status = SupportChatStatus.WAITING_ON_ADMIN
        thread.resolved_at = None

    db.commit()

    created_message = db.get(ChatMessage, message.id)
    if not created_message:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't save the message right now.",
        )
    serialized_message = _serialize_message(created_message, thread)
    realtime_manager.publish_many_user_event_sync(
        [str(thread.customer_user_id), str(thread.vendor_user_id)],
        "chat.message.created",
        serialized_message.model_dump(mode="json"),
    )
    _publish_thread_updates(db, thread)

    if (
        thread.thread_type == ChatThreadType.STORE
        and user.id == thread.customer_user_id
        and thread.vendor_user_id
    ):
        customer = thread.customer_user or db.get(User, thread.customer_user_id)
        if customer:
            try:
                _notify_vendor_shopper_message(
                    db,
                    thread=thread,
                    message=created_message,
                    customer=customer,
                )
            except Exception:
                db.rollback()

    return serialized_message


def update_support_thread_status(
    db: Session,
    user: User,
    thread_id: str,
    payload: SupportChatStatusUpdate,
) -> ChatThreadRead:
    thread = _load_thread(db, thread_id)
    if not thread or thread.thread_type != ChatThreadType.SUPPORT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That support thread was not found.",
        )

    _authorize_thread_participation(thread, user)

    if user.role != UserRole.ADMIN:
        if thread.customer_user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins or the customer can update support thread status.",
            )
        next_status = SupportChatStatus(payload.status)
        if next_status != SupportChatStatus.RESOLVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customers can only mark a support thread as resolved.",
            )
        thread.support_status = next_status
        thread.resolved_at = datetime.now(UTC)
        db.commit()
        refreshed_thread = _load_thread(db, thread.id)
        if not refreshed_thread or not refreshed_thread.vendor_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="We couldn't update the support thread right now.",
            )
        _publish_thread_updates(db, refreshed_thread)
        return _serialize_thread(refreshed_thread, refreshed_thread.vendor_user, unread_count=0)

    next_status = SupportChatStatus(payload.status)
    thread.support_status = next_status
    thread.assigned_admin_user_id = user.id
    if thread.assigned_admin_at is None:
        thread.assigned_admin_at = datetime.now(UTC)
    thread.resolved_at = datetime.now(UTC) if next_status == SupportChatStatus.RESOLVED else None
    db.commit()

    refreshed_thread = _load_thread(db, thread.id)
    if not refreshed_thread or not refreshed_thread.vendor_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't update the support thread right now.",
        )

    _publish_thread_updates(db, refreshed_thread)
    return _serialize_thread(refreshed_thread, refreshed_thread.vendor_user, unread_count=0)
