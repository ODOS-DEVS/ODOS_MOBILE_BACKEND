from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.controllers.catalog_controller import (
    list_active_flash_sale_events,
    list_catalog_products,
    serialize_catalog_products,
)
from app.models import Market, Product, Store
from app.schemas.assistant import AssistantProductRead, AssistantStoreRead
from app.schemas.catalog import ProductRead

PRODUCT_RECOMMENDATION_KEYWORDS = (
    "find",
    "show me",
    "search",
    "look for",
    "looking for",
    "recommend",
    "suggest",
    "under",
    "below",
    "cheap",
    "deal",
    "deals",
    "flash sale",
    "flash-sale",
    "on sale",
    "buy",
    "sneaker",
    "shoe",
    "dress",
    "phone",
    "bag",
)

# Broader shopping keywords used for catalog context (not always product cards).
SHOPPING_INTENT_KEYWORDS = PRODUCT_RECOMMENDATION_KEYWORDS + (
    "shop",
    "product",
    "products",
)

FLASH_SALE_KEYWORDS = ("flash sale", "flash-sale", "on sale", "deals today", "what's on sale")

STORE_INTENT_KEYWORDS = ("store", "stores", "shop", "market", "vendor")

STORE_FACT_PATTERNS = (
    r"\bhow many stores\b",
    r"\bnumber of stores\b",
    r"\bstore count\b",
    r"\bstores (?:are |do we |on |in )?(?:odos|there|available)\b",
    r"\bhow many shops\b",
)

STORE_SCOPED_PHRASE_KEYWORDS = (
    "this store",
    "the store",
    "what do you have",
    "what do they have",
    "what do they sell",
    "what can i buy",
    "anything good",
    "best sellers",
    "on sale",
    "sign in",
)

STORE_SCOPED_WORD_KEYWORDS = (
    "here",
    "their",
    "browse",
    "products",
    "catalogue",
    "catalog",
    "recommend",
    "suggest",
    "popular",
    "bestseller",
    "deals",
    "delivery",
    "chat",
    "message",
)


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _tokenize(query: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(query)) if token]


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def is_store_fact_intent(message: str) -> bool:
    text = _normalize(message)
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in STORE_FACT_PATTERNS)


def is_product_recommendation_intent(message: str, *, store_scoped: bool = False) -> bool:
    text = _normalize(message)
    if not text or is_store_fact_intent(text):
        return False
    if any(keyword in text for keyword in PRODUCT_RECOMMENDATION_KEYWORDS):
        return True
    if store_scoped:
        if any(phrase in text for phrase in STORE_SCOPED_PHRASE_KEYWORDS):
            return True
        product_words = ("product", "products", "recommend", "suggest", "browse", "deal", "deals")
        if any(_has_word(text, word) for word in product_words):
            return True
    return False


def is_shopping_intent(message: str, *, store_scoped: bool = False) -> bool:
    text = _normalize(message)
    if not text or is_store_fact_intent(text):
        return False
    if is_product_recommendation_intent(text, store_scoped=store_scoped):
        return True
    if any(keyword in text for keyword in SHOPPING_INTENT_KEYWORDS):
        return True
    if store_scoped:
        if any(phrase in text for phrase in STORE_SCOPED_PHRASE_KEYWORDS):
            return True
        if any(_has_word(text, word) for word in STORE_SCOPED_WORD_KEYWORDS):
            return True
        accountish = ("order", "voucher", "return", "refund", "wallet", "password", "account")
        if len(text.split()) <= 6 and not any(_has_word(text, token) for token in accountish):
            return True
    return False


def is_flash_sale_intent(message: str) -> bool:
    text = _normalize(message)
    return any(keyword in text for keyword in FLASH_SALE_KEYWORDS)


def is_store_intent(message: str) -> bool:
    text = _normalize(message)
    if is_store_fact_intent(text):
        return False
    return any(keyword in text for keyword in STORE_INTENT_KEYWORDS)


def parse_max_price_cedis(message: str) -> int | None:
    text = _normalize(message)
    patterns = (
        r"(?:under|below|less than|max|maximum)\s*(?:gh[₵c]?|₵|cedis?)?\s*(\d+(?:\.\d+)?)",
        r"(?:gh[₵c]?|₵)\s*(\d+(?:\.\d+)?)\s*(?:or less|max|maximum)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(float(match.group(1)))
            except ValueError:
                continue
    return None


def _product_searchable_text(product: Product, store: Store | None, market: Market | None) -> str:
    parts = [
        product.title,
        product.category,
        product.subcategory,
        " ".join(product.category_slugs or []),
        " ".join(product.subcategory_slugs or []),
        " ".join(product.placement_tags or []),
        product.description,
        store.title if store else None,
        store.category if store else None,
        store.address if store else None,
        market.title if market else None,
    ]
    return " ".join(part for part in parts if part)


def _score_product(query_tokens: list[str], searchable: str) -> int:
    haystack = _normalize(searchable)
    if not haystack:
        return 0
    score = 0
    for token in query_tokens:
        if len(token) < 2:
            continue
        if token in haystack:
            score += 3
        elif any(token in word for word in haystack.split()):
            score += 1
    return score


@dataclass
class CatalogSearchResult:
    products: list[AssistantProductRead]
    stores: list[AssistantStoreRead]
    context_text: str


def _to_assistant_product(item: ProductRead) -> AssistantProductRead:
    return AssistantProductRead(
        id=item.id,
        title=item.title,
        price=item.price,
        old_price=item.old_price,
        discount=item.discount,
        image_url=item.image_url,
        image_key=item.image_key,
        store_id=item.store_id,
        rating=item.rating,
        category=item.category,
    )


def _search_products(
    db: Session,
    *,
    query: str,
    max_price: int | None = None,
    limit: int = 5,
    store_id: str | None = None,
) -> list[ProductRead]:
    tokens = _tokenize(query)
    if not tokens and max_price is None and not store_id:
        return []

    statement: Select[tuple[Product, Store, Market]] = (
        select(Product, Store, Market)
        .outerjoin(Store, Product.store_id == Store.id)
        .outerjoin(Market, Store.market_slug == Market.slug)
        .where(
            Product.is_active.is_(True),
            Product.status == "active",
            Product.stock > 0,
        )
    )
    if store_id:
        statement = statement.where(Product.store_id == store_id)

    if tokens:
        like_filters = []
        for token in tokens[:6]:
            pattern = f"%{token}%"
            like_filters.extend(
                [
                    func.lower(Product.title).like(pattern),
                    func.lower(Product.category).like(pattern),
                    func.lower(Product.subcategory).like(pattern),
                    func.lower(Product.description).like(pattern),
                    func.lower(Store.title).like(pattern),
                    func.lower(Store.address).like(pattern),
                    func.lower(Market.title).like(pattern),
                ]
            )
        statement = statement.where(or_(*like_filters))

    rows = db.execute(statement.limit(120)).all()
    scored: list[tuple[int, Product]] = []
    for product, store, market in rows:
        if max_price is not None and product.price > max_price:
            continue
        score = _score_product(tokens, _product_searchable_text(product, store, market))
        if score > 0 or not tokens:
            scored.append((score, product))

    scored.sort(key=lambda item: (-item[0], item[1].sort_order, item[1].title))
    top_products = [product for _, product in scored[:limit]]
    if not top_products and (max_price is not None or store_id):
        candidates = list_catalog_products(db, store_id=store_id, limit=80)
        filtered = [
            product
            for product in candidates
            if max_price is None or product.price <= max_price
        ]
        filtered.sort(key=lambda product: (product.sort_order, product.price, product.title))
        top_products = filtered[:limit]

    return serialize_catalog_products(db, top_products)


def _search_stores(
    db: Session,
    *,
    query: str,
    limit: int = 5,
    store_id: str | None = None,
) -> list[AssistantStoreRead]:
    if store_id:
        row = db.execute(
            select(Store, Market)
            .outerjoin(Market, Store.market_slug == Market.slug)
            .where(
                Store.id == store_id,
                Store.is_active.is_(True),
                Store.status == "active",
            )
        ).first()
        if not row:
            return []
        store, market = row
        return [
            AssistantStoreRead(
                id=store.id,
                title=store.title,
                category=store.category,
                image_url=store.image_url,
                image_key=store.image_key,
                market_slug=store.market_slug,
                market_title=market.title if market else None,
                rating=store.rating,
            )
        ]

    tokens = _tokenize(query)
    if not tokens:
        return []

    statement = (
        select(Store, Market)
        .outerjoin(Market, Store.market_slug == Market.slug)
        .where(Store.is_active.is_(True), Store.status == "active")
    )
    like_filters = []
    for token in tokens[:6]:
        pattern = f"%{token}%"
        like_filters.extend(
            [
                func.lower(Store.title).like(pattern),
                func.lower(Store.category).like(pattern),
                func.lower(Store.address).like(pattern),
                func.lower(Market.title).like(pattern),
            ]
        )
    statement = statement.where(or_(*like_filters)).limit(40)
    rows = db.execute(statement).all()

    scored: list[tuple[int, Store, Market | None]] = []
    for store, market in rows:
        searchable = " ".join(
            part
            for part in [
                store.title,
                store.category,
                store.address,
                market.title if market else None,
            ]
            if part
        )
        score = _score_product(tokens, searchable)
        if score > 0:
            scored.append((score, store, market))

    scored.sort(key=lambda item: (-item[0], item[1].sort_order, item[1].title))
    results: list[AssistantStoreRead] = []
    for _, store, market in scored[:limit]:
        results.append(
            AssistantStoreRead(
                id=store.id,
                title=store.title,
                category=store.category,
                image_url=store.image_url,
                image_key=store.image_key,
                market_slug=store.market_slug,
                market_title=market.title if market else None,
                rating=store.rating,
            )
        )
    return results


def build_catalog_search_context(
    db: Session,
    message: str,
    *,
    store_id: str | None = None,
    store_name: str | None = None,
) -> CatalogSearchResult:
    store_scoped = bool(store_id)

    # Factual marketplace questions should not pull product carousels.
    if is_store_fact_intent(message):
        active_stores = int(
            db.scalar(
                select(func.count())
                .select_from(Store)
                .where(Store.is_active.is_(True), Store.status == "active")
            )
            or 0
        )
        return CatalogSearchResult(
            products=[],
            stores=[],
            context_text=(
                f"Marketplace stats (live):\n"
                f"- Active stores on ODOS: {active_stores}\n"
                "Answer with this exact count. Do not recommend products unless asked."
            ),
        )

    wants_products = is_product_recommendation_intent(
        message, store_scoped=store_scoped
    ) or is_flash_sale_intent(message)

    if not wants_products and not store_scoped and not is_store_intent(message):
        return CatalogSearchResult(products=[], stores=[], context_text="")

    max_price = parse_max_price_cedis(message)
    products: list[AssistantProductRead] = []
    stores: list[AssistantStoreRead] = []
    lines: list[str] = []

    if store_scoped:
        lines.append(
            f"Catalog scope: ONLY store_id={store_id}"
            + (f' ("{store_name}")' if store_name else "")
            + ". Do not recommend products from other stores."
        )
        stores = _search_stores(db, query=message, limit=1, store_id=store_id)
        if stores:
            location = stores[0].market_title or stores[0].market_slug or "ODOS"
            lines.append(
                f"Focused store: {stores[0].title} · {location} (store_id={stores[0].id})"
            )
        if not wants_products:
            return CatalogSearchResult(
                products=[],
                stores=stores[:1],
                context_text="\n".join(lines),
            )

    if is_flash_sale_intent(message):
        flash_events = list_active_flash_sale_events(db)
        if flash_events and not store_scoped:
            lines.append("Active flash sales:")
            for event in flash_events[:3]:
                lines.append(
                    f"- {event.title} (slug={event.slug}, {event.product_count} products, "
                    f"{event.seconds_remaining}s remaining)"
                )
        flash_products = list_catalog_products(
            db,
            placement="flash-sale",
            store_id=store_id,
            limit=5,
        )
        serialized = serialize_catalog_products(db, flash_products)
        products = [_to_assistant_product(item) for item in serialized]
        if products:
            lines.append("Flash sale products:")
            for item in products:
                lines.append(f"- {item.title} · GH₵{item.price} (product_id={item.id})")

    if not store_scoped and is_store_intent(message):
        stores = _search_stores(db, query=message, limit=5)
        if stores:
            lines.append("Matching stores:")
            for store in stores:
                location = store.market_title or store.market_slug or "ODOS"
                lines.append(f"- {store.title} · {location} (store_id={store.id})")

    if wants_products and not products:
        serialized = _search_products(
            db,
            query=message,
            max_price=max_price,
            limit=5,
            store_id=store_id,
        )
        products = [_to_assistant_product(item) for item in serialized]
        if products:
            label = "Products from this store" if store_scoped else "Matching products"
            lines.append(f"{label}:")
            for item in products:
                lines.append(f"- {item.title} · GH₵{item.price} (product_id={item.id})")

    if wants_products and max_price is not None and not products:
        lines.append(
            f"No catalog matches found under GH₵{max_price}"
            + (" in this store." if store_scoped else ".")
        )
    elif wants_products and store_scoped and not products:
        lines.append("No active products found for this store right now.")

    return CatalogSearchResult(
        products=products[:5] if wants_products else [],
        stores=stores[:5],
        context_text="\n".join(lines),
    )
