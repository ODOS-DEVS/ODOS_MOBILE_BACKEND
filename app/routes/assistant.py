from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.controllers.assistant_controller import get_assistant_status, post_assistant_chat
from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.core.rate_limit import limit_assistant_chat
from app.models import User
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse, AssistantStatusResponse

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/status", response_model=AssistantStatusResponse)
def assistant_status():
    return get_assistant_status()


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    request: Request,
    payload: AssistantChatRequest,
    db: Session = Depends(get_db),
    current_user: Annotated[User | None, Depends(get_optional_current_user)] = None,
):
    limit_assistant_chat(request, current_user)
    return await post_assistant_chat(db, current_user, payload)
