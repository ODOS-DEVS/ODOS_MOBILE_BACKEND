"""Personalized product recommendations from shopper behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.controllers.catalog_controller import list_catalog_products, serialize_catalog_products
from app.models import (
    CartItem,
    Order,
    OrderItem,
    Product,
    User,
    UserBehaviorEvent,
    WishlistItem,
)
from app.schemas.catalog import ProductRead
from app.schemas.recommendation import RecommendationFeedRead

EVENT_WEIGHTS: dict[str, float] = {
    "purchase": 10.0,
    "add_to_cart": 6.0,
    "add_to_wishlist": 5.0,
    "product_view": 2.0,
    "search_result_click": 3.5,
    "product_click": 3.0,
    "remove_from_cart": -2.0,
    "remove_from_wishlist": -1.0,
}

SIGNAL_WINDOW_DAYS = 90
CANDIDATE_POOL_SIZE = 240
MAX_PER_CATEGORY = 4
MIN_STOCK = 1


@dataclass(slots=True)
class UserAffinity:
    categories: dict[str, float] = field(default_factory=dict)
    stores: dict[str, float] = field(default_factory=dict)
    purchased_product_ids: set[str] = field(default_factory=set)
    recent_product_ids: set[str] = field(default_factory=set)
    search_terms: list[str] = field(default_factory=list)

    @property
    def has_signals(self) -> bool:
        return bool(
            self.categories
            or self.stores
            or self.purchased_product_ids
            or self.recent_product_ids
            or self.search_terms
        )


def _normalize_token(value: str | None) -> str:
    if not value:
        return ""
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)


def _category_keys_from_label(value: str | None) -> list[str]:
    if not value:
        return []
    keys: list[str] = []
    for part in value.replace("/", ",").split(","):
        key = _normalize_token(part)
        if key:
            keys.append(key)
    return keys


def _humanize_category_key(key: str) -> str:
    return key.replace("-", " ").strip().title()


def _is_recommendable(product: Product) -> bool:
    if not product.is_active or product.status != "active":
        return False
    if product.stock is not None and product.stock < MIN_STOCK:
        return False
    return True


def _filter_recommendable(products: list[Product]) -> list[Product]:
    return [product for product in products if _is_recommendable(product)]


def _event_recency_multiplier(occurred_at: datetime | None) -> float:
    if not occurred_at:
        return 1.0
    age_days = max(0, (datetime.now(timezone.utc) - occurred_at).days)
    return max(0.2, 1.0 - (age_days / SIGNAL_WINDOW_DAYS) * 0.8)


def _product_category_keys(product: Product) -> list[str]:
    keys: list[str] = []
    if product.category_slugs:
        keys.extend(_normalize_token(slug) for slug in product.category_slugs if slug)
    if product.category:
        keys.extend(_category_keys_from_label(product.category))
    if product.subcategory:
        keys.append(_normalize_token(product.subcategory))
    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _boost_map(target: dict[str, float], keys: list[str], amount: float) -> None:
    for key in keys:
        if not key:
            continue
        target[key] = round(target.get(key, 0.0) + amount, 3)


def _load_user_affinity(db: Session, user: User) -> UserAffinity:
    affinity = UserAffinity()
    since = datetime.now(timezone.utc) - timedelta(days=SIGNAL_WINDOW_DAYS)

    events = list(
        db.scalars(
            select(UserBehaviorEvent)
            .where(
                UserBehaviorEvent.user_id == user.id,
                UserBehaviorEvent.created_at >= since,
            )
            .order_by(UserBehaviorEvent.created_at.desc())
            .limit(500)
        ).all()
    )

    for event in events:
        base_weight = EVENT_WEIGHTS.get(event.event_type, 0.0)
        if base_weight == 0:
            continue

        weight = base_weight * _event_recency_multiplier(event.created_at)

        if event.product_id:
            affinity.recent_product_ids.add(event.product_id)
            if event.event_type == "purchase":
                affinity.purchased_product_ids.add(event.product_id)

        _boost_map(affinity.categories, _category_keys_from_label(event.category), weight)

        if event.store_id:
            affinity.stores[event.store_id] = round(
                affinity.stores.get(event.store_id, 0.0) + weight,
                3,
            )

        if event.event_type == "search_query" and event.search_query:
            affinity.search_terms.append(event.search_query.lower().strip())

    wishlist_rows = db.execute(
        select(WishlistItem.product_id, WishlistItem.category).where(WishlistItem.user_id == user.id)
    ).all()
    for product_id, category in wishlist_rows:
        if product_id:
            affinity.recent_product_ids.add(product_id)
        if category:
            _boost_map(
                affinity.categories,
                _category_keys_from_label(category),
                EVENT_WEIGHTS["add_to_wishlist"],
            )

    cart_rows = db.execute(
        select(CartItem.product_id, CartItem.category).where(CartItem.user_id == user.id)
    ).all()
    for product_id, category in cart_rows:
        if product_id:
            affinity.recent_product_ids.add(product_id)
        if category:
            _boost_map(
                affinity.categories,
                _category_keys_from_label(category),
                EVENT_WEIGHTS["add_to_cart"],
            )

    order_rows = db.execute(
        select(OrderItem.product_id, OrderItem.category, OrderItem.store_id, Order.created_at)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.user_id == user.id,
            Order.payment_status == "paid",
            Order.created_at >= since,
        )
    ).all()
    for product_id, category, store_id, ordered_at in order_rows:
        purchase_weight = EVENT_WEIGHTS["purchase"] * _event_recency_multiplier(ordered_at)
        if product_id:
            affinity.purchased_product_ids.add(product_id)
            affinity.recent_product_ids.add(product_id)
        if category:
            _boost_map(affinity.categories, _category_keys_from_label(category), purchase_weight)
        if store_id:
            affinity.stores[store_id] = round(
                affinity.stores.get(store_id, 0.0) + purchase_weight,
                3,
            )

    affinity.search_terms = affinity.search_terms[-12:]
    return affinity


def _co_purchase_scores(db: Session, seed_product_ids: set[str]) -> dict[str, float]:
    if not seed_product_ids:
        return {}

    since = datetime.now(timezone.utc) - timedelta(days=180)
    rows = db.execute(
        select(
            OrderItem.product_id,
            func.count().label("purchase_count"),
            func.max(Order.created_at).label("last_purchased_at"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.payment_status == "paid",
            Order.created_at >= since,
            Order.id.in_(
                select(OrderItem.order_id).where(OrderItem.product_id.in_(seed_product_ids))
            ),
            OrderItem.product_id.notin_(seed_product_ids),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.count().desc())
        .limit(80)
    ).all()

    if not rows:
        return {}

    max_count = max(int(row.purchase_count) for row in rows) or 1
    now = datetime.now(timezone.utc)
    scores: dict[str, float] = {}

    for row in rows:
        age_days = max(0, (now - row.last_purchased_at).days) if row.last_purchased_at else 180
        recency = max(0.25, 1.0 - age_days / 180)
        scores[row.product_id] = round((int(row.purchase_count) / max_count) * 10.0 * recency, 3)

    return scores


def _catalog_quality_score(product: Product) -> float:
    score = 0.0
    if product.section == "popular":
        score += 4.0
    if product.section == "recommendations":
        score += 3.0
    if product.old_price is not None and product.old_price > product.price:
        score += 3.5
    if product.rating is not None:
        score += min(float(product.rating), 5.0) * 1.5
    if product.stock and product.stock > 0:
        score += min(2.5, 1.0 + (min(product.stock, 20) / 20))
    else:
        score -= 8.0
    if product.updated_at:
        age_days = (datetime.now(timezone.utc) - product.updated_at).days
        score += max(0.0, 8.0 - age_days * 0.08)
    return score


def _search_match_score(product: Product, search_terms: list[str]) -> float:
    if not search_terms:
        return 0.0

    haystack = " ".join(
        filter(
            None,
            [
                product.title,
                product.category,
                product.subcategory,
                " ".join(product.category_slugs or []),
            ],
        )
    ).lower()

    score = 0.0
    for term in search_terms[-10:]:
        normalized = term.strip().lower()
        if not normalized:
            continue
        if normalized in haystack:
            score += 2.8
            continue
        tokens = [token for token in normalized.split() if len(token) >= 3]
        matched_tokens = sum(1 for token in tokens if token in haystack)
        if matched_tokens:
            score += matched_tokens * 1.4
    return score


def _score_product_for_user(
    product: Product,
    affinity: UserAffinity,
    co_purchase: dict[str, float],
) -> float:
    score = _catalog_quality_score(product)

    for key in _product_category_keys(product):
        score += affinity.categories.get(key, 0.0) * 12.0

    if product.store_id:
        score += affinity.stores.get(product.store_id, 0.0) * 8.0

    score += co_purchase.get(product.id, 0.0) * 1.4
    score += _search_match_score(product, affinity.search_terms)

    if product.id in affinity.purchased_product_ids:
        score -= 6.0
    elif product.id in affinity.recent_product_ids:
        score -= 2.0

    return round(score, 3)


def _score_similar_product(
    product: Product,
    anchor: Product,
    affinity: UserAffinity | None,
    co_purchase: dict[str, float],
) -> float:
    score = _catalog_quality_score(product)
    anchor_keys = set(_product_category_keys(anchor))
    product_keys = set(_product_category_keys(product))
    overlap = anchor_keys.intersection(product_keys)
    score += len(overlap) * 14.0

    if anchor.subcategory and product.subcategory:
        if _normalize_token(anchor.subcategory) == _normalize_token(product.subcategory):
            score += 10.0

    if anchor.store_id and product.store_id == anchor.store_id:
        score += 8.0

    score += co_purchase.get(product.id, 0.0) * 1.8

    if affinity:
        for key in product_keys:
            score += affinity.categories.get(key, 0.0) * 4.0
        if product.store_id:
            score += affinity.stores.get(product.store_id, 0.0) * 3.0

    return round(score, 3)


def _pick_diverse_products(products: list[Product], limit: int) -> list[Product]:
    picked: list[Product] = []
    category_counts: dict[str, int] = {}

    for product in products:
        category_keys = _product_category_keys(product)
        primary_category = category_keys[0] if category_keys else "general"
        if category_counts.get(primary_category, 0) >= MAX_PER_CATEGORY:
            continue
        picked.append(product)
        category_counts[primary_category] = category_counts.get(primary_category, 0) + 1
        if len(picked) >= limit:
            break

    if len(picked) < limit:
        seen = {product.id for product in picked}
        for product in products:
            if product.id in seen:
                continue
            picked.append(product)
            if len(picked) >= limit:
                break

    return picked


def _cold_start_products(db: Session, limit: int) -> list[Product]:
    popular = list_catalog_products(db, section="popular", limit=limit * 2)
    curated = list_catalog_products(db, section="recommendations", limit=limit * 2)

    merged: dict[str, Product] = {}
    for product in [*popular, *curated]:
        if _is_recommendable(product):
            merged[product.id] = product

    if len(merged) < limit:
        extras = list_catalog_products(db, limit=limit * 3)
        for product in extras:
            if _is_recommendable(product):
                merged.setdefault(product.id, product)

    ranked = sorted(
        merged.values(),
        key=lambda product: (
            _catalog_quality_score(product),
            product.updated_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return _pick_diverse_products(ranked, limit)


def _candidate_products(db: Session, affinity: UserAffinity) -> list[Product]:
    top_categories = sorted(affinity.categories.items(), key=lambda item: item[1], reverse=True)[:4]
    candidates: dict[str, Product] = {}

    for category_key, _ in top_categories:
        if not category_key:
            continue
        for product in list_catalog_products(db, category=category_key, limit=80):
            if _is_recommendable(product):
                candidates[product.id] = product

    for store_id, _ in sorted(affinity.stores.items(), key=lambda item: item[1], reverse=True)[:3]:
        for product in list_catalog_products(db, store_id=store_id, limit=60):
            if _is_recommendable(product):
                candidates[product.id] = product

    for product in _cold_start_products(db, CANDIDATE_POOL_SIZE):
        candidates.setdefault(product.id, product)

    if len(candidates) < min(80, CANDIDATE_POOL_SIZE):
        for product in list_catalog_products(db, limit=CANDIDATE_POOL_SIZE):
            if _is_recommendable(product):
                candidates.setdefault(product.id, product)

    return list(candidates.values())


def get_for_you_recommendations(
    db: Session,
    user: User | None,
    *,
    limit: int = 12,
) -> RecommendationFeedRead:
    if user is None or not user.personalization_enabled:
        products = _cold_start_products(db, limit)
        return RecommendationFeedRead(
            title="Popular on ODOS",
            subtitle="Trending picks across the marketplace",
            personalized=False,
            products=serialize_catalog_products(db, products),
        )

    affinity = _load_user_affinity(db, user)
    if not affinity.has_signals:
        products = _cold_start_products(db, limit)
        return RecommendationFeedRead(
            title="Popular on ODOS",
            subtitle="Browse more to unlock picks tailored to you",
            personalized=False,
            products=serialize_catalog_products(db, products),
        )

    seed_ids = affinity.purchased_product_ids or affinity.recent_product_ids
    co_purchase = _co_purchase_scores(db, set(seed_ids))
    candidates = _candidate_products(db, affinity)

    ranked = sorted(
        _filter_recommendable(candidates),
        key=lambda product: _score_product_for_user(product, affinity, co_purchase),
        reverse=True,
    )
    picked = _pick_diverse_products(ranked, limit)

    if len(picked) < limit:
        for product in _cold_start_products(db, limit):
            if all(existing.id != product.id for existing in picked):
                picked.append(product)
            if len(picked) >= limit:
                break

    top_category = (
        max(affinity.categories.items(), key=lambda item: item[1])[0]
        if affinity.categories
        else None
    )
    subtitle = "Based on what you browse, save, and buy"
    if top_category:
        subtitle = f"Because you shop {_humanize_category_key(top_category)} and more"

    return RecommendationFeedRead(
        title="For you",
        subtitle=subtitle,
        personalized=True,
        products=serialize_catalog_products(db, picked),
    )


def get_similar_product_recommendations(
    db: Session,
    user: User | None,
    product_id: str,
    *,
    limit: int = 8,
) -> RecommendationFeedRead:
    anchor = db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.is_active.is_(True),
            Product.status == "active",
        )
    )
    if not anchor:
        return RecommendationFeedRead(
            title="More like this",
            subtitle="Similar picks from across ODOS",
            personalized=False,
            products=[],
        )

    affinity = _load_user_affinity(db, user) if user and user.personalization_enabled else None
    co_purchase = _co_purchase_scores(db, {product_id})

    category_keys = _product_category_keys(anchor)
    candidates: dict[str, Product] = {}

    for category_key in category_keys[:2]:
        for product in list_catalog_products(db, category=category_key, limit=100):
            if product.id != product_id and _is_recommendable(product):
                candidates[product.id] = product

    if anchor.store_id:
        for product in list_catalog_products(db, store_id=anchor.store_id, limit=60):
            if product.id != product_id and _is_recommendable(product):
                candidates.setdefault(product.id, product)

    for co_product_id in co_purchase:
        if co_product_id == product_id:
            continue
        product = db.get(Product, co_product_id)
        if product and _is_recommendable(product):
            candidates.setdefault(product.id, product)

    if not candidates:
        for product in _cold_start_products(db, limit + 4):
            if product.id != product_id:
                candidates[product.id] = product

    ranked = sorted(
        candidates.values(),
        key=lambda product: _score_similar_product(product, anchor, affinity, co_purchase),
        reverse=True,
    )
    picked = _pick_diverse_products(ranked, limit)

    personalized = affinity is not None and affinity.has_signals
    return RecommendationFeedRead(
        title="More like this",
        subtitle="Similar items you may want to explore next",
        personalized=personalized,
        products=serialize_catalog_products(db, picked),
    )
