from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.models import User
from app.schemas.assistant import AssistantChatRequest
from app.services.assistant_service import chat_with_assistant


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


async def iter_assistant_chat_stream(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AsyncIterator[str]:
    response = await chat_with_assistant(db, user, payload)
    words = response.reply.split()
    accumulated = ""
    for index, word in enumerate(words):
        accumulated = word if index == 0 else f"{accumulated} {word}"
        yield _sse_event("token", {"text": accumulated})
        await asyncio.sleep(0.018)

    yield _sse_event(
        "done",
        {
            "reply": response.reply,
            "suggested_actions": [action.model_dump() for action in response.suggested_actions],
            "escalated_to_support": response.escalated_to_support,
            "conversation_id": response.conversation_id,
            "message_id": response.message_id,
            "products": [product.model_dump() for product in response.products],
            "stores": [store.model_dump() for store in response.stores],
        },
    )
