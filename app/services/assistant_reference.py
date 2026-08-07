"""Resolve and format assistant screen references (store, product, or checkout focus)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market, Product, Store
from app.schemas.assistant import AssistantReferenceContext


def normalize_reference_context(
    context: AssistantReferenceContext | dict | None,
) -> dict[str, str] | None:
    if context is None:
        return None
    if isinstance(context, AssistantReferenceContext):
        data = context.model_dump(exclude_none=True)
    elif isinstance(context, dict):
        data = {key: value for key, value in context.items() if value not in (None, "")}
    else:
        return None

    ref_type = str(data.get("type") or "store").strip() or "store"
    store_id = str(data.get("store_id") or "").strip()
    product_id = str(data.get("product_id") or "").strip()

    if ref_type == "product" and product_id:
        normalized: dict[str, str] = {"type": "product", "product_id": product_id}
        if store_id:
            normalized["store_id"] = store_id
        for key in ("product_title", "store_name", "category"):
            value = data.get(key)
            if value is not None and str(value).strip():
                normalized[key] = str(value).strip()
        return normalized

    if ref_type == "checkout":
        return {"type": "checkout"}

    if not store_id:
        return None

    normalized = {"type": "store", "store_id": store_id}
    for key in ("store_name", "market_title", "vendor_user_id", "category"):
        value = data.get(key)
        if value is not None and str(value).strip():
            normalized[key] = str(value).strip()
    return normalized


def _resolve_store(db: Session, normalized: dict[str, str]) -> dict[str, str]:
    store = db.scalar(select(Store).where(Store.id == normalized["store_id"]))
    if store is None or not store.is_active:
        return normalized

    market = None
    if store.market_slug:
        market = db.scalar(select(Market).where(Market.slug == store.market_slug))

    resolved: dict[str, str] = {
        "type": "store",
        "store_id": store.id,
        "store_name": store.title,
    }
    if store.category:
        resolved["category"] = store.category
    if store.city:
        resolved["city"] = store.city
    if store.region:
        resolved["region"] = store.region
    if store.address:
        resolved["address"] = store.address
    if store.description:
        resolved["description"] = store.description
    if store.rating is not None:
        resolved["rating"] = f"{store.rating:.1f}"
    if market and market.title:
        resolved["market_title"] = market.title
    elif store.market_slug:
        resolved["market_title"] = store.market_slug
    if store.vendor_user_id:
        resolved["vendor_user_id"] = str(store.vendor_user_id)

    # Prefer live store fields; keep client labels only as soft fallbacks.
    if not resolved.get("store_name") and normalized.get("store_name"):
        resolved["store_name"] = normalized["store_name"]
    if not resolved.get("market_title") and normalized.get("market_title"):
        resolved["market_title"] = normalized["market_title"]

    return resolved


def _resolve_product(db: Session, normalized: dict[str, str]) -> dict[str, str]:
    product = db.scalar(select(Product).where(Product.id == normalized["product_id"]))
    if product is None:
        return normalized

    resolved: dict[str, str] = {
        "type": "product",
        "product_id": product.id,
        "product_title": product.title,
    }
    if product.price is not None:
        resolved["price"] = str(product.price)
    if product.category:
        resolved["category"] = product.category
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
        if store is not None:
            resolved["store_id"] = store.id
            resolved["store_name"] = store.title
            if store.vendor_user_id:
                resolved["vendor_user_id"] = str(store.vendor_user_id)

    if not resolved.get("product_title") and normalized.get("product_title"):
        resolved["product_title"] = normalized["product_title"]
    if not resolved.get("store_name") and normalized.get("store_name"):
        resolved["store_name"] = normalized["store_name"]

    return resolved


def resolve_store_reference(
    db: Session,
    context: AssistantReferenceContext | dict | None,
) -> dict[str, str] | None:
    """Resolve a client-supplied reference (store, product, or checkout) against
    live DB rows, enriching/overriding client-supplied labels with authoritative
    data. Name kept for backward compatibility with existing call sites even
    though it now resolves more than just stores."""
    normalized = normalize_reference_context(context)
    if not normalized:
        return None

    ref_type = normalized.get("type")
    if ref_type == "checkout":
        return normalized
    if ref_type == "product":
        return _resolve_product(db, normalized)
    return _resolve_store(db, normalized)


def format_reference_context_block(reference: dict[str, str] | None) -> str:
    if not reference:
        return ""

    ref_type = reference.get("type", "store")

    if ref_type == "checkout":
        return (
            "FOCUSED SCREEN REFERENCE (high priority):\n"
            "- The shopper is currently on the Checkout screen.\n"
            "- Prioritize checkout-related help: delivery fees, vouchers, payment methods, address, order review.\n"
            "- The exact delivery fee and total are shown live on that screen — do not invent numbers."
        )

    if ref_type == "product" and reference.get("product_id"):
        product_title = reference.get("product_title") or "this product"
        lines = [
            "FOCUSED SCREEN REFERENCE (high priority):",
            f'- The shopper is viewing the product "{product_title}" (product_id={reference["product_id"]}).',
            '- When they say "this product", "this item", or "here", they mean THIS product.',
            "- Answer using the product details below. Don't recommend unrelated products unless "
            "they clearly ask to browse or compare.",
        ]
        details = []
        if reference.get("price"):
            details.append(f"Price: GH₵{reference['price']}")
        if reference.get("category"):
            details.append(f"Category: {reference['category']}")
        if reference.get("store_name"):
            details.append(f"Sold by: {reference['store_name']}")
        if details:
            lines.append("Product details:")
            lines.extend(f"- {item}" for item in details)
        return "\n".join(lines)

    if not reference.get("store_id"):
        return ""

    store_name = reference.get("store_name") or "this store"
    lines = [
        "FOCUSED SCREEN REFERENCE (high priority):",
        f'- The shopper opened the assistant from "{store_name}" (store_id={reference["store_id"]}).',
        '- When they say "this store", "here", "their products", or ask generally about products/deals/delivery/chat, they mean THIS store.',
        "- Product recommendations MUST come only from this store unless they clearly ask to compare with other stores or browse ODOS broadly.",
        "- Prefer deep links back to this store and its products. Include chat-with-store actions using vendor_user_id when available.",
    ]
    details = []
    for label, key in (
        ("Category", "category"),
        ("Market", "market_title"),
        ("City", "city"),
        ("Region", "region"),
        ("Address", "address"),
        ("Rating", "rating"),
        ("Vendor user id", "vendor_user_id"),
    ):
        value = reference.get(key)
        if value:
            details.append(f"{label}: {value}")
    if reference.get("description"):
        details.append(f"About: {reference['description']}")
    if details:
        lines.append("Store details:")
        lines.extend(f"- {item}" for item in details)
    return "\n".join(lines)
