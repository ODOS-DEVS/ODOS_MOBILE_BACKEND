from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.controllers.chat_controller import (
    ensure_store_chat_thread,
    ensure_support_chat_thread,
    list_chat_messages,
    list_chat_threads,
    post_chat_message,
    update_support_thread_status,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.chat import (
    ChatMessageRead,
    SupportChatStatusUpdate,
    SupportChatThreadEnsurePayload,
    ChatThreadEnsurePayload,
    ChatThreadRead,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/threads", response_model=list[ChatThreadRead])
def get_chat_threads(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    scope: Literal["customer", "vendor", "support"] = Query(default="customer"),
):
    return list_chat_threads(db, current_user, scope=scope)


@router.post(
    "/threads/store/{store_id}",
    response_model=ChatThreadRead,
    status_code=status.HTTP_201_CREATED,
)
def create_or_get_store_thread(
    store_id: str,
    payload: ChatThreadEnsurePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return ensure_store_chat_thread(db, current_user, store_id, payload)


@router.post(
    "/threads/support",
    response_model=ChatThreadRead,
    status_code=status.HTTP_201_CREATED,
)
def create_or_get_support_thread(
    payload: SupportChatThreadEnsurePayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return ensure_support_chat_thread(db, current_user, payload)


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessageRead])
def get_thread_messages(
    thread_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_chat_messages(db, current_user, thread_id)


@router.post(
    "/threads/{thread_id}/messages",
    response_model=ChatMessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread_message(
    thread_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    body: Annotated[str | None, Form(max_length=2000)] = None,
    attachment_duration_seconds: Annotated[int | None, Form(ge=0, le=3600)] = None,
    attachment: UploadFile | None = None,
):
    return await post_chat_message(
        db,
        current_user,
        thread_id,
        body=body,
        attachment=attachment,
        attachment_duration_seconds=attachment_duration_seconds,
    )


@router.patch("/threads/{thread_id}/support-status", response_model=ChatThreadRead)
def patch_support_thread_status(
    thread_id: str,
    payload: SupportChatStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_support_thread_status(db, current_user, thread_id, payload)
