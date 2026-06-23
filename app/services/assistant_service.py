from __future__ import annotations

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
        base_url = settings.openrouter_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://odos.app"
        headers["X-Title"] = "ODOS Mobile Assistant"
        # Free/small OpenRouter models often reject response_format — JSON is enforced via the system prompt.
    elif provider == "ollama":
        base_url = settings.ollama_base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        request_body["format"] = "json"
    else:
        api_key = settings.openai_api_key.strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured.")
        url = "https://api.openai.com/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        request_body["response_format"] = {"type": "json_object"}

    timeout = 90.0 if provider == "ollama" else 45.0

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=request_body)
        response.raise_for_status()
        payload = response.json()

    content = payload["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


def assistant_is_enabled() -> bool:
    return settings.assistant_is_configured


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
        if exc.response is not None:
            detail = exc.response.text[:500]
        logger.warning(
            "Assistant LLM HTTP error %s: %s",
            exc.response.status_code if exc.response is not None else "unknown",
            detail or str(exc),
        )
        return AssistantChatResponse(
            reply=(
                "I'm having trouble reaching the AI service right now. "
                "Try again in a moment, or chat with our support team."
            ),
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
