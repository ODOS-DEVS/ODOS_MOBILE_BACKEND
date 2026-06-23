from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse, AssistantStatusResponse
from app.services.assistant_service import assistant_is_enabled, chat_with_assistant, probe_assistant_llm


async def post_assistant_chat(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AssistantChatResponse:
    return await chat_with_assistant(db, user, payload)


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

