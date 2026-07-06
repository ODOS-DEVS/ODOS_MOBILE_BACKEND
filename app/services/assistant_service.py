from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CartItem, Order, User
from app.schemas.assistant import (
    AssistantActionRead,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantMessageInput,
)

logger = logging.getLogger(__name__)

ODOS_APP_GUIDE = """
You are ODOS Assistant — the in-app guide for the ODOS marketplace mobile app (Ghana-focused e-commerce).

Your job:
- Help shoppers and vendors use the app confidently.
- Answer questions about orders, checkout, delivery, returns, vouchers, wallet, stores, deals, and account settings.
- Give short, friendly, practical answers (2–5 sentences unless the user asks for detail).
- When you don't know something specific to their account, say what screen to open or suggest human support.
- Never invent order numbers, balances, or policies. Use only the user context provided.
- Currency is Ghana Cedis (GH₵ / GHS).

App areas (use these route values in suggested_actions when helpful):
- Home: /(root)/(tabs)
- Categories: /(root)/(tabs)/category
- Cart: /(root)/(tabs)/cart
- Wishlist: /(root)/(tabs)/wishlist
- Profile: /(root)/(tabs)/profile
- Search: /screens/search
- Deals: /screens/deals
- Recommendations: /screens/recommendation
- Orders: /screens/profileScreens/orders
- Returns: /screens/profileScreens/Account/Returns
- Addresses: /screens/profileScreens/Account/Addresses
- Wallet & payment: /screens/profileScreens/Account/Wallet
- Vouchers: /screens/profileScreens/Account/Vouchers
- Chats: /screens/profileScreens/Account/Chats
- FAQ: /screens/profileScreens/helpAndSupport/FAQ
- Human support chat: /screens/support/chat
- Vendor dashboard: /vendor/dashboard

Delivery on ODOS:
- Standard (3–5 business days), Express (1–2 days), Same-day in Greater Accra when address qualifies.
- Free standard shipping on larger orders; exact fee shown at checkout.

Returns:
- Based on ODOS return policy and seller rules; start from Profile > Returns or contact the store chat.

When the user needs a person (payment dispute, account block, damaged item escalation), set escalated_to_support true and include the support chat action.

Respond ONLY with valid JSON:
{
  "reply": "your message to the user",
  "suggested_actions": [{"label": "Short label", "route": "/screens/..."}],
  "escalated_to_support": false
}
""".strip()


def _build_user_context(db: Session, user: User | None) -> str:
    if user is None:
        return "User is browsing as a guest (not signed in)."

    roles = ", ".join(user.roles or ["customer"])
    lines = [
        f"Signed-in user: {user.full_name or user.email}",
        f"Roles: {roles}",
    ]

    order_count = db.scalar(
        select(func.count()).select_from(Order).where(Order.user_id == user.id)
    )
    lines.append(f"Total orders: {int(order_count or 0)}")

    latest_order = db.scalar(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(desc(Order.created_at))
        .limit(1)
    )
    if latest_order:
        lines.append(
            f"Latest order: #{latest_order.order_number}, status {latest_order.status}, "
            f"total GH₵{latest_order.total_amount:.2f}"
        )

    cart_count = db.scalar(
        select(func.count()).select_from(CartItem).where(CartItem.user_id == user.id)
    )
    lines.append(f"Cart items: {int(cart_count or 0)}")

    if "vendor" in (user.roles or []):
        lines.append("User has vendor access — can manage store from the Store tab.")

    return "\n".join(lines)


def _parse_llm_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    return {
        "reply": content.strip() or "I couldn't format that answer. Please try again.",
        "suggested_actions": [],
        "escalated_to_support": False,
    }


def _fallback_reply(payload: AssistantChatRequest, user: User | None) -> AssistantChatResponse:
    text = payload.message.strip().lower()

    if any(word in text for word in ("order", "tracking", "delivery status")):
        if user is None:
            reply = (
                "Sign in first so I can help with your orders. Then open Profile > Orders "
                "to see live status and tracking."
            )
            actions = [
                AssistantActionRead(label="Sign in", route="/(root)/(auth)/signin"),
            ]
        else:
            reply = (
                "Open Profile > Orders to see every order, status, and receipt. "
                "Tap an order for tracking updates and store chat."
            )
            actions = [
                AssistantActionRead(label="View orders", route="/screens/profileScreens/orders"),
            ]
        return AssistantChatResponse(reply=reply, suggested_actions=actions)

    if "voucher" in text or "promo" in text or "coupon" in text:
        return AssistantChatResponse(
            reply=(
                "Claim vouchers from Deals or your voucher wallet, then enter the code at checkout "
                "or tap Browse Wallet on the checkout screen."
            ),
            suggested_actions=[
                AssistantActionRead(label="My vouchers", route="/screens/profileScreens/Account/Vouchers"),
                AssistantActionRead(label="Browse deals", route="/screens/deals"),
            ],
        )

    if "return" in text or "refund" in text:
        return AssistantChatResponse(
            reply=(
                "Start a return from Profile > Returns. Choose the order item, explain the issue, "
                "and the store or ODOS team will review it."
            ),
            suggested_actions=[
                AssistantActionRead(label="Returns", route="/screens/profileScreens/Account/Returns"),
            ],
        )

    if "human" in text or "agent" in text or "support" in text or "person" in text:
        return AssistantChatResponse(
            reply="I'll connect you with the ODOS support team for hands-on help.",
            suggested_actions=[
                AssistantActionRead(label="Chat with support", route="/screens/support/chat"),
            ],
            escalated_to_support=True,
        )

    return AssistantChatResponse(
        reply=(
            "I'm the ODOS Assistant. Ask me about orders, checkout, delivery, vouchers, returns, "
            "stores, or how to use any part of the app. For account-specific issues, sign in first."
        ),
        suggested_actions=[
            AssistantActionRead(label="FAQ", route="/screens/profileScreens/helpAndSupport/FAQ"),
            AssistantActionRead(label="Get help", route="/screens/profileScreens/helpAndSupport/GetHelp"),
        ],
    )


OPENROUTER_FALLBACK_MODELS = (
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-2-9b-it:free",
    "mistralai/mistral-7b-instruct:free",
)

GEMINI_FALLBACK_MODELS = (
    "gemini-3-flash-preview",
)

GEMINI_RETRY_DELAYS_SECONDS = (1.5,)


def _is_gemini_capacity_error(status_code: int, body: str) -> bool:
    if status_code == 429:
        return True

    lowered = body.lower()
    capacity_markers = (
        "resource_exhausted",
        "resource has been exhausted",
        "quota exceeded",
        "exceeded your current quota",
        "rate limit",
        "rate-limit",
        "too many requests",
    )
    if status_code in {403, 503} and any(marker in lowered for marker in capacity_markers):
        return True
    return any(marker in lowered for marker in capacity_markers)


def _gemini_error_message(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code in {401, 403} and "api key" in lowered:
        return (
            "The Gemini API key on the server is invalid or missing. "
            "Create a free key at aistudio.google.com/apikey and set GEMINI_API_KEY on Render."
        )
    if _is_gemini_capacity_error(status_code, body):
        return (
            "Gemini free-tier quota is used up for now. "
            "Wait a few minutes and try again."
        )
    if status_code == 404 or "not found" in lowered:
        return (
            "The configured Gemini model is no longer available. "
            "Set ASSISTANT_MODEL=gemini-3.1-flash-lite on Render and redeploy."
        )
    if status_code == 400:
        return (
            "Gemini rejected the request (HTTP 400). "
            "Check ASSISTANT_MODEL and redeploy with a valid GEMINI_API_KEY."
        )
    if status_code in {502, 503, 504}:
        return (
            f"Gemini is temporarily unavailable (HTTP {status_code}). "
            "Try again in a minute."
        )
    if status_code:
        return (
            f"I'm having trouble reaching Gemini (HTTP {status_code}). "
            "Verify GEMINI_API_KEY at aistudio.google.com/apikey."
        )
    return (
        "I'm having trouble reaching the AI service right now. "
        "Try again in a moment, or chat with our support team."
    )


def _provider_error_message(provider: str, status_code: int, body: str) -> str:
    if provider == "gemini":
        return _gemini_error_message(status_code, body)
    if provider == "openrouter":
        return _openrouter_error_message(status_code, body)
    return (
        "I'm having trouble reaching the AI service right now. "
        "Try again in a moment, or chat with our support team."
    )


def _messages_to_gemini_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        role = message["role"]
        content = message["content"]
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.35,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
        },
    }
    if system_parts:
        payload["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_parts)}],
        }
    return payload


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    content = "".join(text_parts).strip()
    if not content:
        raise RuntimeError("Gemini returned an empty response.")
    return content


async def _call_gemini(
    *,
    model: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured.")

    base_url = settings.gemini_api_base
    request_body = _messages_to_gemini_payload(messages)
    models_to_try: list[str] = []
    for candidate in (model, *GEMINI_FALLBACK_MODELS):
        if candidate not in models_to_try:
            models_to_try.append(candidate)

    last_error: httpx.HTTPStatusError | None = None

    async with httpx.AsyncClient(timeout=45.0) as client:
        for candidate_model in models_to_try:
            url = f"{base_url}/models/{candidate_model}:generateContent"
            retries = (*GEMINI_RETRY_DELAYS_SECONDS, None)

            for attempt_index, delay_seconds in enumerate(retries):
                response = await client.post(
                    url,
                    params={"key": api_key},
                    headers={"Content-Type": "application/json"},
                    json=request_body,
                )
                if response.is_success:
                    payload = response.json()
                    content = _extract_gemini_text(payload)
                    return _parse_llm_json(content)

                if (
                    delay_seconds is not None
                    and _is_gemini_capacity_error(response.status_code, response.text)
                ):
                    logger.info(
                        "Gemini capacity limit on %s (HTTP %s), retrying in %ss",
                        candidate_model,
                        response.status_code,
                        delay_seconds,
                    )
                    await asyncio.sleep(delay_seconds)
                    continue

                last_error = httpx.HTTPStatusError(
                    f"Gemini error for {candidate_model}",
                    request=response.request,
                    response=response,
                )
                logger.warning(
                    "Gemini model %s failed (HTTP %s): %s",
                    candidate_model,
                    response.status_code,
                    response.text[:240],
                )
                if response.status_code not in {404, 429, 403, 503}:
                    break
                break

    if last_error is not None:
        raise last_error

    raise RuntimeError("No Gemini model candidates were available.")


async def _probe_gemini() -> tuple[bool, str | None]:
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        return False, "Assistant is not configured (missing GEMINI_API_KEY)."

    model = settings.assistant_model_name
    url = f"{settings.gemini_api_base}/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 8},
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
        if response.is_success:
            return True, None
        return False, _gemini_error_message(response.status_code, response.text[:500])
    except httpx.RequestError as exc:
        return False, f"Could not reach Gemini ({exc.__class__.__name__}). Check GEMINI_BASE_URL."
    except Exception as exc:
        logger.warning("Assistant Gemini probe error: %s", exc)
        return False, "Unexpected error while probing Gemini."


def _openrouter_error_message(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code == 401:
        return (
            "The OpenRouter API key on the server is invalid or expired. "
            "Update OPENROUTER_API_KEY in Render and redeploy."
        )
    if status_code == 402:
        return (
            "OpenRouter needs account credits even for some free models. "
            "Open openrouter.ai/settings/credits, add a small balance, then try again."
        )
    if status_code == 429:
        return "OpenRouter rate limit reached. Wait a minute and try again."
    if status_code == 404:
        return (
            "That OpenRouter model is unavailable right now. "
            "Try ASSISTANT_MODEL=google/gemma-2-9b-it:free on Render."
        )
    if "response_format" in lowered or "json_object" in lowered:
        return "The selected model does not support structured JSON mode. Redeploy the latest backend fix."
    if status_code == 400:
        return (
            "OpenRouter rejected the request (HTTP 400). "
            "Check ASSISTANT_MODEL on Render or try google/gemma-2-9b-it:free."
        )
    if status_code in {502, 503, 504}:
        return (
            f"OpenRouter is temporarily unavailable (HTTP {status_code}). "
            "Try again in a minute."
        )
    if status_code:
        return (
            f"I'm having trouble reaching the AI service (HTTP {status_code}). "
            "Check OPENROUTER_API_KEY and credits on openrouter.ai, then try again."
        )
    return (
        "I'm having trouble reaching the AI service right now. "
        "Try again in a moment, or chat with our support team."
    )


async def _call_llm(
    *,
    user_context: str,
    screen: str | None,
    history: list[AssistantMessageInput],
    message: str,
) -> dict[str, Any]:
    provider = settings.assistant_provider_normalized
    model = settings.assistant_model_name

    messages: list[dict[str, str]] = [
        {"role": "system", "content": ODOS_APP_GUIDE},
        {
            "role": "system",
            "content": (
                f"Current user context:\n{user_context}\n\n"
                f"Current screen: {screen or 'unknown'}"
            ),
        },
    ]

    for item in history[-10:]:
        messages.append({"role": item.role, "content": item.content})

    messages.append({"role": "user", "content": message})

    if provider == "gemini":
        return await _call_gemini(model=model, messages=messages)

    request_body = {
        "model": model,
        "temperature": 0.35,
        "messages": messages,
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}

    if provider == "openrouter":
        api_key = settings.openrouter_api_key.strip()
        if not api_key:
            raise RuntimeError("OpenRouter API key is not configured.")
        base_url = settings.openrouter_api_base
        url = f"{base_url}/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://odos.app"
        headers["X-Title"] = "ODOS Mobile Assistant"
        # Free/small OpenRouter models often reject response_format — JSON is enforced via the system prompt.
    elif provider == "ollama":
        base_url = settings.ollama_base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        request_body["format"] = "json"
    elif provider == "openai":
        api_key = settings.openai_api_key.strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured.")
        url = "https://api.openai.com/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        request_body["response_format"] = {"type": "json_object"}
    else:
        raise RuntimeError(f"Unsupported assistant provider: {provider}")

    timeout = 90.0 if provider == "ollama" else 45.0

    models_to_try = [model]
    if provider == "openrouter":
        models_to_try = []
        for candidate in (model, *OPENROUTER_FALLBACK_MODELS):
            if candidate not in models_to_try:
                models_to_try.append(candidate)

    last_error: httpx.HTTPStatusError | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for candidate_model in models_to_try:
            attempt_body = {**request_body, "model": candidate_model}
            response = await client.post(url, headers=headers, json=attempt_body)
            if response.is_success:
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                return _parse_llm_json(content)

            last_error = httpx.HTTPStatusError(
                f"OpenRouter error for {candidate_model}",
                request=response.request,
                response=response,
            )
            if response.status_code not in {404, 502, 503}:
                break

    if last_error is not None:
        raise last_error

    raise RuntimeError("No OpenRouter model candidates were available.")


def assistant_is_enabled() -> bool:
    return settings.assistant_is_configured


async def probe_assistant_llm() -> tuple[bool, str | None]:
    """Lightweight provider ping for /assistant/status?probe=1 diagnostics."""
    if not assistant_is_enabled():
        return False, "Assistant is not configured (missing API key)."

    provider = settings.assistant_provider_normalized
    if provider == "gemini":
        return await _probe_gemini()
    if provider == "openrouter":
        api_key = settings.openrouter_api_key.strip()
        base_url = settings.openrouter_api_base
        url = f"{base_url}/chat/completions"
        model = settings.assistant_model_name

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://odos.app",
            "X-Title": "ODOS Mobile Assistant",
        }
        body = {
            "model": model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "ping"}],
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=body)
            if response.is_success:
                return True, None
            return False, _openrouter_error_message(response.status_code, response.text[:500])
        except httpx.RequestError as exc:
            return False, f"Could not reach OpenRouter ({exc.__class__.__name__}). Check OPENROUTER_BASE_URL."
        except Exception as exc:
            logger.warning("Assistant probe error: %s", exc)
            return False, "Unexpected error while probing OpenRouter."

    return True, None


async def chat_with_assistant(
    db: Session,
    user: User | None,
    payload: AssistantChatRequest,
) -> AssistantChatResponse:
    user_context = _build_user_context(db, user)

    if not assistant_is_enabled():
        return _fallback_reply(payload, user)

    try:
        parsed = await _call_llm(
            user_context=user_context,
            screen=payload.screen,
            history=payload.history,
            message=payload.message,
        )
    except httpx.HTTPStatusError as exc:
        detail = ""
        status_code = exc.response.status_code if exc.response is not None else 0
        provider = settings.assistant_provider_normalized
        if exc.response is not None:
            detail = exc.response.text[:500]
        logger.warning(
            "Assistant LLM HTTP error %s: %s",
            status_code or "unknown",
            detail or str(exc),
        )
        if (
            status_code in {402, 404, 429, 502, 503, 504}
            or (provider == "gemini" and _is_gemini_capacity_error(status_code, detail))
        ):
            return _fallback_reply(payload, user)
        return AssistantChatResponse(
            reply=_provider_error_message(provider, status_code, detail),
            suggested_actions=[
                AssistantActionRead(label="Contact support", route="/screens/support/chat"),
            ],
        )
    except Exception as exc:
        logger.warning("Assistant error: %s", exc)
        return _fallback_reply(payload, user)

    actions = [
        AssistantActionRead(label=item["label"], route=item["route"])
        for item in parsed.get("suggested_actions", [])
        if isinstance(item, dict) and item.get("label") and item.get("route")
    ]

    reply = str(parsed.get("reply", "")).strip()
    if not reply:
        return _fallback_reply(payload, user)

    return AssistantChatResponse(
        reply=reply,
        suggested_actions=actions[:4],
        escalated_to_support=bool(parsed.get("escalated_to_support")),
    )
