from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.controllers.delivery_controller import get_delivery_quote
from app.controllers.order_controller import get_order, list_orders
from app.controllers.vendor_controller import fetch_vendor_dashboard, list_vendor_orders
from app.controllers.voucher_controller import list_user_vouchers
from app.models import User
from app.schemas.delivery import DeliveryQuoteRequest
from app.services.assistant_catalog_search import build_catalog_search_context, parse_max_price_cedis

MAX_TOOL_LOOP = 5

GEMINI_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": "Search the ODOS product catalog by keywords and optional max price in GH₵.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords, e.g. sneakers, dress, phone"},
                "max_price_cedis": {
                    "type": "number",
                    "description": "Optional maximum price in Ghana cedis",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_stores",
        "description": "Search ODOS stores by name, category, city, or market.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Store search keywords, e.g. Kumasi fashion"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_orders",
        "description": "List the signed-in customer's orders, optionally filtered by status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional order status filter, e.g. pending, processing, delivered",
                },
                "limit": {"type": "integer", "description": "Max orders to return (default 5, max 10)"},
            },
        },
    },
    {
        "name": "get_order",
        "description": "Get full details for a specific customer order by order_id UUID.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID from context or list_orders"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_voucher_wallet",
        "description": "List the signed-in customer's saved vouchers and promo codes.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "estimate_delivery",
        "description": "Estimate delivery options and fees for a Ghana region and cart subtotal.",
        "parameters": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Delivery region, e.g. Greater Accra, Ashanti",
                },
                "subtotal_cedis": {
                    "type": "number",
                    "description": "Cart subtotal in GH₵ (default 100)",
                },
            },
        },
    },
    {
        "name": "get_vendor_dashboard",
        "description": "Get live vendor dashboard stats: pending orders, products, wallet balance. Vendor accounts only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_vendor_orders",
        "description": "List orders for the vendor's store. Vendor accounts only.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter, e.g. pending, confirmed, processing",
                },
                "limit": {"type": "integer", "description": "Max orders (default 5, max 10)"},
            },
        },
    },
]


def gemini_tools_payload(*, include_vendor_tools: bool) -> list[dict[str, Any]]:
    declarations = GEMINI_TOOL_DECLARATIONS
    if not include_vendor_tools:
        declarations = [
            tool
            for tool in declarations
            if tool["name"] not in {"get_vendor_dashboard", "list_vendor_orders"}
        ]
    return [{"functionDeclarations": declarations}]


def _require_user(user: User | None, tool_name: str) -> User:
    if user is None:
        raise PermissionError(f"Sign in required to use {tool_name}.")
    return user


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def execute_assistant_tool(
    db: Session,
    user: User | None,
    *,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    try:
        if name == "search_products":
            query = str(args.get("query", "")).strip()
            max_price = args.get("max_price_cedis")
            if max_price is None:
                max_price = parse_max_price_cedis(query)
            result = build_catalog_search_context(db, query)
            if max_price is not None:
                filtered = [product for product in result.products if product.price <= int(max_price)]
                if filtered:
                    result.products = filtered
            return {
                "products": [_json_safe(product) for product in result.products[:5]],
                "context": result.context_text,
            }

        if name == "search_stores":
            query = str(args.get("query", "")).strip()
            result = build_catalog_search_context(db, f"stores {query}")
            return {
                "stores": [_json_safe(store) for store in result.stores[:5]],
                "context": result.context_text,
            }

        if name == "list_orders":
            signed_in = _require_user(user, name)
            limit = min(int(args.get("limit") or 5), 10)
            status_filter = str(args.get("status") or "").strip().lower()
            orders = list_orders(db, signed_in)
            if status_filter:
                orders = [order for order in orders if order.status.lower() == status_filter]
            payload = []
            for order in orders[:limit]:
                payload.append(
                    {
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "status": order.status,
                        "total_amount": float(order.total_amount),
                        "tracking_eta": order.tracking_eta,
                        "items": [item.title for item in order.items[:5]],
                    }
                )
            return {"orders": payload, "count": len(payload)}

        if name == "get_order":
            signed_in = _require_user(user, name)
            order_id = str(args.get("order_id", "")).strip()
            order = get_order(db, signed_in, order_id)
            return {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "status": order.status,
                "total_amount": float(order.total_amount),
                "tracking_eta": order.tracking_eta,
                "delivery_method": order.delivery_method,
                "address": {
                    "street": order.address_street,
                    "city": order.address_city,
                    "region": order.address_region,
                },
                "items": [
                    {
                        "title": item.title,
                        "quantity": item.quantity,
                        "price": float(item.price),
                        "store_id": item.store_id,
                    }
                    for item in order.items
                ],
            }

        if name == "get_voucher_wallet":
            signed_in = _require_user(user, name)
            vouchers = list_user_vouchers(db, signed_in)
            return {
                "vouchers": [
                    {
                        "code": voucher.code,
                        "title": voucher.title,
                        "reward_text": voucher.reward_text,
                        "expires_at": voucher.expires_at.isoformat() if voucher.expires_at else None,
                    }
                    for voucher in vouchers[:10]
                ]
            }

        if name == "estimate_delivery":
            region = str(args.get("region") or "Greater Accra").strip()
            subtotal = float(args.get("subtotal_cedis") or 100)
            quote = get_delivery_quote(
                DeliveryQuoteRequest(subtotal=subtotal, region=region),
                db,
            )
            return _json_safe(quote)

        if name == "get_vendor_dashboard":
            signed_in = _require_user(user, name)
            dashboard = fetch_vendor_dashboard(db, signed_in)
            return _json_safe(dashboard)

        if name == "list_vendor_orders":
            signed_in = _require_user(user, name)
            limit = min(int(args.get("limit") or 5), 10)
            status_filter = str(args.get("status") or "").strip().lower()
            orders = list_vendor_orders(db, signed_in)
            if status_filter:
                orders = [order for order in orders if order.status.lower() == status_filter]
            return {
                "orders": [
                    {
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "status": order.status,
                        "total_amount": order.total_amount,
                        "customer_name": order.customer_name,
                        "product_count": order.product_count,
                    }
                    for order in orders[:limit]
                ]
            }

        return {"error": f"Unknown tool: {name}"}
    except PermissionError as exc:
        return {"error": str(exc)}
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except Exception as exc:
        return {"error": f"Tool failed: {exc.__class__.__name__}"}


def serialize_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)
