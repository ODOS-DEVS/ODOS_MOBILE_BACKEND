from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse, AssistantStatusResponse
from app.services.assistant_service import assistant_is_enabled, chat_with_assistant


async def post_assistant_chat(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AssistantChatResponse:
    return await chat_with_assistant(db, user, payload)


def get_assistant_status() -> AssistantStatusResponse:
    enabled = assistant_is_enabled()
    provider = settings.assistant_provider_normalized if enabled else "fallback"
    return AssistantStatusResponse(
        enabled=enabled,
        provider=provider,
        model=settings.assistant_model_name if enabled else None,
    )

