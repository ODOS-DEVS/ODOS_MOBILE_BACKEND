from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.models import User
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse
from app.services.assistant_service import chat_with_assistant, stream_assistant_reply

logger = logging.getLogger(__name__)


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _done_payload(response: AssistantChatResponse) -> dict:
    return {
        "reply": response.reply,
        "suggested_actions": [action.model_dump() for action in response.suggested_actions],
        "escalated_to_support": response.escalated_to_support,
        "conversation_id": response.conversation_id,
        "message_id": response.message_id,
        "products": [product.model_dump() for product in response.products],
        "stores": [store.model_dump() for store in response.stores],
    }


async def _iter_simulated_stream(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AsyncIterator[str]:
    """Fallback path: generate the full reply, then replay it word by word. Used
    whenever real token streaming isn't available (non-Gemini provider, or the
    live stream failed for any reason) — this is the original, proven behavior."""
    response = await chat_with_assistant(db, user, payload)
    words = response.reply.split()
    accumulated = ""
    for index, word in enumerate(words):
        accumulated = word if index == 0 else f"{accumulated} {word}"
        yield _sse_event("token", {"text": accumulated})
        await asyncio.sleep(0.018)

    yield _sse_event("done", _done_payload(response))


async def iter_assistant_chat_stream(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AsyncIterator[str]:
    try:
        async for kind, value in stream_assistant_reply(db, user, payload):
            if kind == "token":
                yield _sse_event("token", {"text": value})
            elif kind == "done":
                yield _sse_event("done", _done_payload(value))
        return
    except Exception as exc:
        logger.info("Real assistant streaming unavailable, falling back: %s", exc)

    async for event in _iter_simulated_stream(db, user, payload):
        yield event
