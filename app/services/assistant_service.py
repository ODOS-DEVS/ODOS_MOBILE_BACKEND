from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User
from app.services.assistant_context import AssistantUserSnapshot, build_assistant_user_context
from app.schemas.assistant import (
    AssistantActionRead,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantMessageInput,
    AssistantProductRead,
    AssistantStoreRead,
)
from app.services.assistant_catalog_search import CatalogSearchResult, build_catalog_search_context
from app.services.assistant_tools import (
    MAX_TOOL_LOOP,
    execute_assistant_tool,
    gemini_tools_payload,
)
from app.services.assistant_memory import (
    append_conversation_message,
    get_or_create_conversation,
    history_from_conversation,
)

logger = logging.getLogger(__name__)

ODOS_APP_GUIDE = """
You are ODOS Assistant — a warm, clear, trusted in-app guide for ODOS, a Ghana-focused marketplace app.

Personality:
- Friendly and practical, like a helpful shop assistant in Accra — never robotic or overly long.
- Lead with the direct answer first, then the next step.
- Use GH₵ / GHS for money. Keep most replies to 2–4 sentences unless the user asks for detail.
- Never invent order numbers, voucher codes, balances, ETAs, or policies. Use ONLY the user context below.
- If the user is a guest, guide them to sign in before personal order or voucher help.
- If the user is a vendor, prioritize vendor dashboard, orders, products, returns, and payouts language.

When user context includes recent orders, cart items, vouchers, returns, or address — reference them by name/number naturally.
Example: "Your order #ODOS-1234 is out for delivery" not "check the orders screen".

When the user is a vendor, you are also their **Vendor Assistant**:
- Answer with live numbers from vendor dashboard context and vendor tools (pending orders, wallet, products).
- Guide them to /vendor/dashboard, /vendor/orders, /vendor/products, /vendor/wallet, /vendor/returns.
- Do not give shopper-only advice unless they explicitly ask as a customer.

You have live tools — call them when you need fresh data instead of guessing:
- search_products, search_stores — catalog discovery
- list_orders, get_order — customer order lookup (signed-in only)
- get_voucher_wallet — saved vouchers
- estimate_delivery — delivery fees by region
- get_vendor_dashboard, list_vendor_orders — vendor accounts only

After using tools, respond in the required JSON format below.

When the user asks to find products, stores, deals, or flash sales, use tools or catalog search results below.
Recommend 1–3 specific items by name and price. Do not invent products not listed in catalog search results.
Product cards are shown in the app automatically — keep reply concise and reference what you found.

Deep links — use suggested_actions with optional params for one-tap navigation:
- Specific order: route "/(root)/screens/profileScreens/orders/[orderId]", params {"orderId": "<uuid>"}
- Orders list: route "/screens/profileScreens/orders"
- Cart: route "/(root)/(tabs)/cart"
- Checkout: route "/(root)/screens/Checkout"
- Vouchers wallet: route "/screens/profileScreens/Account/Vouchers"
- Returns: route "/screens/profileScreens/Account/Returns"
- Deals: route "/screens/deals"
- Search: route "/screens/search"
- Store chat: route "/screens/productDetails/chat/[vendorId]", params {"vendorId": "<uuid>", "vendorName": "Store name"}
- Human support: route "/screens/support/chat", params {"subject": "short summary"}
- Vendor dashboard: route "/vendor/dashboard"
- Sign in: route "/(root)/(auth)/signin"

Delivery on ODOS:
- Standard (3–5 business days), Express (1–2 days), Same-day in Greater Accra when the address qualifies.
- Exact delivery fee is always shown at checkout.

Returns:
- Start from Profile > Returns or chat with the store for item-specific issues.

When the user needs a person (payment dispute, account block, damaged item escalation), set escalated_to_support true and include the support chat action.

Respond ONLY with valid JSON:
{
  "reply": "your message to the user",
  "suggested_actions": [{"label": "Short label", "route": "/screens/...", "params": {"optional": "value"}}],
  "escalated_to_support": false
}
""".strip()


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


def _serialize_assistant_actions(items: list[Any]) -> list[AssistantActionRead]:
    actions: list[AssistantActionRead] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        route = str(item.get("route", "")).strip()
        if not label or not route:
            continue
        raw_params = item.get("params")
        params: dict[str, str] | None = None
        if isinstance(raw_params, dict):
            cleaned = {
                str(key): str(value).strip()
                for key, value in raw_params.items()
                if value is not None and str(value).strip()
            }
            params = cleaned or None
        actions.append(AssistantActionRead(label=label[:80], route=route[:160], params=params))
    return actions[:4]


def _build_chat_response(
    *,
    reply: str,
    actions: list[AssistantActionRead],
    escalated: bool = False,
    products: list[AssistantProductRead] | None = None,
    stores: list[AssistantStoreRead] | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> AssistantChatResponse:
    return AssistantChatResponse(
        reply=reply,
        suggested_actions=actions[:4],
        escalated_to_support=escalated,
        products=products or [],
        stores=stores or [],
        conversation_id=conversation_id,
        message_id=message_id,
    )


def _message_metadata(
    *,
    actions: list[AssistantActionRead],
    products: list[AssistantProductRead],
    stores: list[AssistantStoreRead],
    escalated: bool,
) -> dict[str, Any]:
    return {
        "suggested_actions": [action.model_dump() for action in actions],
        "products": [product.model_dump() for product in products],
        "stores": [store.model_dump() for store in stores],
        "escalated_to_support": escalated,
    }


def _fallback_reply(
    payload: AssistantChatRequest,
    user: User | None,
    snapshot: AssistantUserSnapshot | None = None,
    catalog: CatalogSearchResult | None = None,
) -> AssistantChatResponse:
    text = payload.message.strip().lower()

    if catalog and (catalog.products or catalog.stores):
        if catalog.products:
            preview = ", ".join(product.title for product in catalog.products[:3])
            reply = (
                f"I found {len(catalog.products)} matching products"
                f"{f', including {preview}' if preview else ''}. "
                "Tap a product below to view details."
            )
            actions = [
                AssistantActionRead(label="Browse deals", route="/screens/deals"),
                AssistantActionRead(label="Search", route="/screens/search"),
            ]
        else:
            preview = ", ".join(store.title for store in catalog.stores[:3])
            reply = (
                f"I found {len(catalog.stores)} matching stores"
                f"{f', including {preview}' if preview else ''}. "
                "Tap a store below to explore."
            )
            actions = [
                AssistantActionRead(label="Search stores", route="/screens/search"),
            ]
        return _build_chat_response(
            reply=reply,
            actions=actions,
            products=catalog.products,
            stores=catalog.stores,
        )

    if any(word in text for word in ("order", "tracking", "delivery status")):
        if user is None:
            reply = (
                "Sign in first so I can help with your orders. Then open Profile > Orders "
                "to see live status and tracking."
            )
            actions = [
                AssistantActionRead(label="Sign in", route="/(root)/(auth)/signin"),
            ]
        elif snapshot and snapshot.latest_order_id:
            reply = (
                f"Your latest order is #{snapshot.latest_order_number}. "
                "Open it for live status, receipt, and store chat."
            )
            actions = [
                AssistantActionRead(
                    label=f"Order #{snapshot.latest_order_number}",
                    route="/(root)/screens/profileScreens/orders/[orderId]",
                    params={"orderId": snapshot.latest_order_id},
                ),
                AssistantActionRead(label="All orders", route="/screens/profileScreens/orders"),
            ]
            if snapshot.latest_vendor_user_id:
                actions.append(
                    AssistantActionRead(
                        label="Chat store",
                        route="/screens/productDetails/chat/[vendorId]",
                        params={"vendorId": snapshot.latest_vendor_user_id},
                    )
                )
        else:
            reply = (
                "Open Profile > Orders to see every order, status, and receipt. "
                "Tap an order for tracking updates and store chat."
            )
            actions = [
                AssistantActionRead(label="View orders", route="/screens/profileScreens/orders"),
            ]
        return _build_chat_response(reply=reply, actions=actions[:4])

    if "voucher" in text or "promo" in text or "coupon" in text:
        if snapshot and snapshot.best_voucher_code:
            reply = (
                f"You have voucher {snapshot.best_voucher_code} in your wallet. "
                "Enter it at checkout or tap Browse Wallet on the checkout screen."
            )
            actions = [
                AssistantActionRead(label="Go to checkout", route="/(root)/screens/Checkout"),
                AssistantActionRead(
                    label="My vouchers",
                    route="/screens/profileScreens/Account/Vouchers",
                ),
            ]
        else:
            reply = (
                "Claim vouchers from Deals or your voucher wallet, then enter the code at checkout "
                "or tap Browse Wallet on the checkout screen."
            )
            actions = [
                AssistantActionRead(
                    label="My vouchers",
                    route="/screens/profileScreens/Account/Vouchers",
                ),
                AssistantActionRead(label="Browse deals", route="/screens/deals"),
            ]
        return _build_chat_response(reply=reply, actions=actions)

    if "return" in text or "refund" in text:
        return _build_chat_response(
            reply=(
                "Start a return from Profile > Returns. Choose the order item, explain the issue, "
                "and the store or ODOS team will review it."
            ),
            actions=[
                AssistantActionRead(label="Returns", route="/screens/profileScreens/Account/Returns"),
            ],
        )

    if "human" in text or "agent" in text or "support" in text or "person" in text:
        return _build_chat_response(
            reply="I'll connect you with the ODOS support team for hands-on help.",
            actions=[
                AssistantActionRead(label="Chat with support", route="/screens/support/chat"),
            ],
            escalated=True,
        )

    return _build_chat_response(
        reply=(
            "I'm the ODOS Assistant. Ask me about orders, checkout, delivery, vouchers, returns, "
            "stores, or how to use any part of the app. For account-specific issues, sign in first."
        ),
        actions=[
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


def _messages_to_gemini_payload(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = True,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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

    generation_config: dict[str, Any] = {
        "temperature": 0.35,
        "maxOutputTokens": 768,
    }
    if json_mode:
        generation_config["responseMimeType"] = "application/json"

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if system_parts:
        payload["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_parts)}],
        }
    if tools:
        payload["tools"] = tools
    return payload


def _parse_gemini_parts(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")
    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts: list[str] = []
    function_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            text_parts.append(str(part["text"]))
        if part.get("functionCall"):
            function_calls.append(part["functionCall"])
    return "".join(text_parts).strip(), function_calls


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    text, function_calls = _parse_gemini_parts(payload)
    if function_calls:
        raise RuntimeError("Gemini returned function calls instead of text.")
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


async def _post_gemini(
    *,
    client: httpx.AsyncClient,
    model: str,
    request_body: dict[str, Any],
    stream: bool = False,
) -> httpx.Response:
    endpoint = "streamGenerateContent" if stream else "generateContent"
    suffix = "?alt=sse" if stream else ""
    url = f"{settings.gemini_api_base}/models/{model}:{endpoint}{suffix}"
    return await client.post(
        url,
        params={"key": settings.gemini_api_key.strip()},
        headers={"Content-Type": "application/json"},
        json=request_body,
    )


async def _call_gemini_with_tools(
    *,
    db: Session,
    user: User | None,
    model: str,
    messages: list[dict[str, str]],
    is_vendor: bool,
) -> dict[str, Any]:
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured.")

    tools = gemini_tools_payload(include_vendor_tools=is_vendor)
    request_body = _messages_to_gemini_payload(messages, json_mode=False, tools=tools)
    contents = list(request_body["contents"])

    models_to_try: list[str] = []
    for candidate in (model, *GEMINI_FALLBACK_MODELS):
        if candidate not in models_to_try:
            models_to_try.append(candidate)

    last_error: httpx.HTTPStatusError | None = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        for candidate_model in models_to_try:
            working_body = {**request_body, "contents": contents}
            for _ in range(MAX_TOOL_LOOP):
                response = await _post_gemini(
                    client=client,
                    model=candidate_model,
                    request_body=working_body,
                )
                if not response.is_success:
                    last_error = httpx.HTTPStatusError(
                        f"Gemini error for {candidate_model}",
                        request=response.request,
                        response=response,
                    )
                    break

                payload = response.json()
                text, function_calls = _parse_gemini_parts(payload)
                if function_calls:
                    model_parts = [{"functionCall": call} for call in function_calls]
                    contents.append({"role": "model", "parts": model_parts})
                    response_parts = []
                    for call in function_calls:
                        name = str(call.get("name", "")).strip()
                        args = call.get("args") if isinstance(call.get("args"), dict) else {}
                        result = execute_assistant_tool(db, user, name=name, args=args)
                        response_parts.append(
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {"result": result},
                                }
                            }
                        )
                    contents.append({"role": "user", "parts": response_parts})
                    working_body = {**request_body, "contents": contents}
                    continue

                if text:
                    return _parse_llm_json(text)

                break

            if last_error is not None and last_error.response.status_code not in {404, 429, 403, 503}:
                break

        if last_error is not None:
            raise last_error

    # Final formatting pass — ask for JSON after tool loop
    contents.append(
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        "Using the tool results above, respond to the user now. "
                        "Return ONLY valid JSON with reply, suggested_actions, escalated_to_support."
                    )
                }
            ],
        }
    )
    final_body = _messages_to_gemini_payload(messages, json_mode=True)
    final_body["contents"] = contents
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await _post_gemini(client=client, model=model, request_body=final_body)
        response.raise_for_status()
        text, _ = _parse_gemini_parts(response.json())
        return _parse_llm_json(text)


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
    db: Session,
    user: User | None,
    user_context: str,
    catalog_context: str,
    screen: str | None,
    history: list[AssistantMessageInput],
    message: str,
    is_vendor: bool = False,
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
    if catalog_context.strip():
        messages.append(
            {
                "role": "system",
                "content": f"Catalog search results:\n{catalog_context}",
            }
        )

    for item in history[-10:]:
        messages.append({"role": item.role, "content": item.content})

    messages.append({"role": "user", "content": message})

    if provider == "gemini":
        return await _call_gemini_with_tools(
            db=db,
            user=user,
            model=model,
            messages=messages,
            is_vendor=is_vendor,
        )

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
    user_context, snapshot = build_assistant_user_context(db, user)
    catalog = build_catalog_search_context(db, payload.message)

    conversation = None
    conversation_id: str | None = None
    if user is not None:
        conversation = get_or_create_conversation(
            db,
            user,
            conversation_id=payload.conversation_id,
            screen=payload.screen,
        )
        conversation_id = str(conversation.id)

    if conversation is not None:
        history_payload = history_from_conversation(db, conversation.id, limit=10)
        history = [AssistantMessageInput(role=item["role"], content=item["content"]) for item in history_payload]
    else:
        history = payload.history

    def _persist_assistant_response(response: AssistantChatResponse) -> AssistantChatResponse:
        if conversation is None:
            return response
        append_conversation_message(
            db,
            conversation,
            role="user",
            content=payload.message,
        )
        assistant_message = append_conversation_message(
            db,
            conversation,
            role="assistant",
            content=response.reply,
            metadata=_message_metadata(
                actions=response.suggested_actions,
                products=response.products,
                stores=response.stores,
                escalated=response.escalated_to_support,
            ),
        )
        db.commit()
        response.conversation_id = conversation_id
        response.message_id = str(assistant_message.id)
        return response

    if not assistant_is_enabled():
        return _persist_assistant_response(_fallback_reply(payload, user, snapshot, catalog))

    try:
        parsed = await _call_llm(
            db=db,
            user=user,
            user_context=user_context,
            catalog_context=catalog.context_text,
            screen=payload.screen,
            history=history,
            message=payload.message,
            is_vendor=bool(snapshot and snapshot.is_vendor),
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
            return _persist_assistant_response(_fallback_reply(payload, user, snapshot, catalog))
        return _build_chat_response(
            reply=_provider_error_message(provider, status_code, detail),
            actions=[
                AssistantActionRead(label="Contact support", route="/screens/support/chat"),
            ],
            conversation_id=conversation_id,
        )
    except Exception as exc:
        logger.warning("Assistant error: %s", exc)
        return _persist_assistant_response(_fallback_reply(payload, user, snapshot, catalog))

    actions = _serialize_assistant_actions(parsed.get("suggested_actions", []))
    reply = str(parsed.get("reply", "")).strip()
    if not reply:
        return _persist_assistant_response(_fallback_reply(payload, user, snapshot, catalog))

    return _persist_assistant_response(
        _build_chat_response(
            reply=reply,
            actions=actions,
            escalated=bool(parsed.get("escalated_to_support")),
            products=catalog.products,
            stores=catalog.stores,
            conversation_id=conversation_id,
        )
    )
