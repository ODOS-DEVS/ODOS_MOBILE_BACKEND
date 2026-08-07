import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.controllers.assistant_controller import (
    get_assistant_session,
    get_assistant_status,
    post_assistant_chat,
    post_assistant_feedback,
    stream_assistant_chat,
)
from app.core.auth import get_current_user, get_optional_current_user
from app.core.database import get_db
from app.core.rate_limit import limit_assistant_chat
from app.models import User
from app.schemas.assistant import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantFeedbackRequest,
    AssistantFeedbackResponse,
    AssistantReferenceContext,
    AssistantSessionResponse,
    AssistantStatusResponse,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/status", response_model=AssistantStatusResponse)
async def assistant_status(probe: bool = False):
    return await get_assistant_status(probe=probe)


@router.get("/session", response_model=AssistantSessionResponse)
def assistant_session(
    db: Session = Depends(get_db),
    current_user: Annotated[User | None, Depends(get_optional_current_user)] = None,
    conversation_id: uuid.UUID | None = None,
    screen: str | None = None,
    store_id: str | None = None,
    store_name: str | None = None,
    market_title: str | None = None,
    vendor_user_id: str | None = None,
    product_id: str | None = None,
    product_title: str | None = None,
    category: str | None = None,
    context_type: str | None = None,
):
    context = None
    if product_id:
        context = AssistantReferenceContext(
            type="product",
            product_id=product_id,
            product_title=product_title,
            store_id=store_id,
            store_name=store_name,
            category=category,
        )
    elif context_type == "checkout":
        context = AssistantReferenceContext(type="checkout")
    elif store_id:
        context = AssistantReferenceContext(
            type="store",
            store_id=store_id,
            store_name=store_name,
            market_title=market_title,
            vendor_user_id=vendor_user_id,
            category=category,
        )
    return get_assistant_session(
        db,
        current_user,
        conversation_id=conversation_id,
        screen=screen,
        context=context,
    )


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    request: Request,
    payload: AssistantChatRequest,
    db: Session = Depends(get_db),
    current_user: Annotated[User | None, Depends(get_optional_current_user)] = None,
):
    limit_assistant_chat(request, current_user)
    return await post_assistant_chat(db, current_user, payload)


@router.post("/chat/stream")
async def assistant_chat_stream(
    request: Request,
    payload: AssistantChatRequest,
    db: Session = Depends(get_db),
    current_user: Annotated[User | None, Depends(get_optional_current_user)] = None,
):
    limit_assistant_chat(request, current_user)
    return StreamingResponse(
        stream_assistant_chat(db, current_user, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/feedback", response_model=AssistantFeedbackResponse)
def assistant_feedback(
    payload: AssistantFeedbackRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return post_assistant_feedback(db, current_user, payload)
