import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from app.schemas.assistant import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantFeedbackRequest,
    AssistantFeedbackResponse,
    AssistantSessionResponse,
    AssistantStatusResponse,
)
from app.services.assistant_memory import build_assistant_session, save_message_feedback
from app.services.assistant_service import assistant_is_enabled, chat_with_assistant, probe_assistant_llm
from app.services.assistant_stream import iter_assistant_chat_stream


async def post_assistant_chat(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AssistantChatResponse:
    return await chat_with_assistant(db, user, payload)


async def stream_assistant_chat(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
):
    async for chunk in iter_assistant_chat_stream(db, user, payload):
        yield chunk


async def get_assistant_status(*, probe: bool = False) -> AssistantStatusResponse:
    enabled = assistant_is_enabled()
    provider = settings.assistant_provider_normalized if enabled else "fallback"
    llm_reachable: bool | None = None
    llm_error: str | None = None

    if probe and enabled:
        llm_reachable, llm_error = await probe_assistant_llm()

    return AssistantStatusResponse(
        enabled=enabled,
        provider=provider,
        model=settings.assistant_model_name if enabled else None,
        llm_reachable=llm_reachable,
        llm_error=llm_error,
    )


def get_assistant_session(
    db: Session,
    user: User | None,
    *,
    conversation_id: uuid.UUID | None = None,
    screen: str | None = None,
) -> AssistantSessionResponse:
    response = build_assistant_session(
        db,
        user,
        conversation_id=conversation_id,
        screen=screen,
    )
    db.commit()
    return response


def post_assistant_feedback(
    db: Session,
    user: User,
    payload: AssistantFeedbackRequest,
) -> AssistantFeedbackResponse:
    try:
        rating = save_message_feedback(db, user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    db.commit()
    return AssistantFeedbackResponse(message_id=str(payload.message_id), rating=rating)
