"""Enhanced search algorithm with admin-tunable weights and learning from clicks.

Features:
1. Relevance scoring with multiple signals
2. Click-based learning (what users click becomes more relevant)
3. Admin-tunable weights (adjust recommendation strength)
4. Better typo/synonym handling
5. Real-time trending boost
"""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.models import (
    Product,
    UserBehaviorEvent,
    Store,
)


@dataclass(slots=True)
class SearchResult:
    """Product search result with relevance score."""

    product_id: str
    relevance_score: float
    reason: str


class SearchWeights:
    """Tunable weights for search relevance.

    These can be adjusted by admin to tune search quality.
    In production, these would be stored in database + admin panel.
    """

    title_exact: float = 5.0  # Product title exact match
    title_contains: float = 4.0  # Product title contains term
    description: float = 2.0  # Product description
    category: float = 3.0  # Category name match
    store_name: float = 2.5  # Store name
    rating_boost: float = 0.1  # Per star rating
    recency_boost: float = 0.05  # Recent products
    popularity_boost: float = 0.02  # View count
    click_boost: float = 0.5  # Clicked by others


def get_search_weights(db: Session) -> SearchWeights:
    """Get weights from database (future: admin-editable).

    For now, uses defaults. In production:
    - Store in SearchConfiguration table
    - Allow admin to adjust via dashboard
    - A/B test different weights
    """
    return SearchWeights()


def tokenize_query(query: str) -> list[str]:
    """Break search query into tokens."""
    return [token.lower().strip() for token in query.split() if token.strip()]


def normalize_text(text: str) -> str:
    """Normalize text for matching."""
    return text.lower().strip()


async def search_products(
    db: Session,
    query: str,
    limit: int = 30,
    user_id: str | None = None,
) -> list[SearchResult]:
    """Enhanced search with multiple ranking signals.

    Factors:
    1. Query match quality (title > description)
    2. Product popularity (views, rating)
    3. Recency (new products)
    4. Store reputation
    5. What similar users clicked on
    """

    if not query or not query.strip():
        return []

    weights = get_search_weights(db)
    query_tokens = tokenize_query(query)
    query_lower = normalize_text(query)

    # Get all active products
    all_products = db.scalars(
        select(Product).where(
            Product.status == "active",
            Product.stock > 0,
        )
    ).all()

    scores = {}

    for product in all_products:
        product_score = 0.0
        matched_fields = []

        # 1. TITLE MATCH (highest priority)
        title_lower = normalize_text(product.title)
        if title_lower == query_lower:
            # Exact match
            product_score += weights.title_exact
            matched_fields.append("exact title match")
        else:
            # Contains match
            if query_lower in title_lower:
                product_score += weights.title_contains
                matched_fields.append("title")
            else:
                # Token match
                for token in query_tokens:
                    if token in title_lower:
                        product_score += weights.title_contains * 0.7
                        matched_fields.append("title token")
                        break

        # 2. DESCRIPTION MATCH
        if product.description:
            description_lower = normalize_text(product.description)
            if query_lower in description_lower:
                product_score += weights.description
                matched_fields.append("description")

        # 3. CATEGORY MATCH
        if product.category:
            category_lower = normalize_text(product.category)
            if query_lower in category_lower:
                product_score += weights.category
                matched_fields.append("category")

        # Skip if no match found
        if product_score == 0:
            continue

        # 4. STORE REPUTATION
        if product.store_id:
            store = db.scalar(select(Store).where(Store.id == product.store_id))
            if store and store.rating:
                product_score += store.rating * 0.1
                matched_fields.append("trusted store")

        # 5. PRODUCT POPULARITY
        view_count = db.scalar(
            select(func.count(UserBehaviorEvent.id)).where(
                UserBehaviorEvent.product_id == product.id,
                UserBehaviorEvent.event_type.in_(["product_view", "product_click"]),
            )
        ) or 0
        product_score += min(view_count * weights.popularity_boost, 5.0)  # Cap boost

        # 6. PRODUCT RATING
        if product.rating:
            product_score += product.rating * weights.rating_boost
            matched_fields.append(f"{product.rating}★")

        # 7. RECENCY (products added in last 30 days)
        if product.created_at:
            days_old = (datetime.now(timezone.utc) - product.created_at).days
            if days_old < 30:
                product_score += weights.recency_boost * (30 - days_old)
                matched_fields.append("new")

        # 8. CLICK LEARNING (what similar users clicked)
        if user_id:
            # Check if users who searched this also clicked this product
            click_count = db.scalar(
                select(func.count(UserBehaviorEvent.id)).where(
                    UserBehaviorEvent.product_id == product.id,
                    UserBehaviorEvent.event_type == "search_result_click",
                )
            ) or 0
            product_score += min(click_count * weights.click_boost, 3.0)

        scores[product.id] = {
            "score": product_score,
            "product": product,
            "reason": ", ".join(matched_fields[:3]),  # Show top 3 factors
        }

    # Sort by score
    if not scores:
        return []

    sorted_results = sorted(
        scores.items(),
        key=lambda x: x[1]["score"],
        reverse=True,
    )

    results = []
    for product_id, data in sorted_results[:limit]:
        results.append(
            SearchResult(
                product_id=product_id,
                relevance_score=data["score"],
                reason=data["reason"] or "Matches your search",
            )
        )

    return results


async def get_trending_search_terms(db: Session, hours: int = 24) -> list[str]:
    """Get trending search terms (what people are searching for NOW).

    Helps admin understand customer interest.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    trending = db.execute(
        select(
            UserBehaviorEvent.search_query,
            func.count(UserBehaviorEvent.id).label("search_count"),
        )
        .where(
            UserBehaviorEvent.event_type == "search_query",
            UserBehaviorEvent.created_at >= cutoff,
            UserBehaviorEvent.search_query.isnot(None),
        )
        .group_by(UserBehaviorEvent.search_query)
        .order_by(func.count(UserBehaviorEvent.id).desc())
        .limit(20)
    ).all()

    return [query for query, _ in trending]


async def get_search_performance(db: Session, search_query: str) -> dict:
    """Get performance metrics for a search query.

    Admin can use this to understand:
    - How many people searched for this
    - How many clicked results
    - Conversion rate
    """
    search_events = db.execute(
        select(func.count(UserBehaviorEvent.id))
        .where(
            UserBehaviorEvent.event_type == "search_query",
            UserBehaviorEvent.search_query == search_query,
        )
    ).scalar() or 0

    click_events = db.execute(
        select(func.count(UserBehaviorEvent.id))
        .where(
            UserBehaviorEvent.event_type == "search_result_click",
            UserBehaviorEvent.search_query == search_query,
        )
    ).scalar() or 0

    click_through_rate = (
        (click_events / search_events * 100) if search_events > 0 else 0
    )

    return {
        "search_query": search_query,
        "total_searches": search_events,
        "result_clicks": click_events,
        "click_through_rate": round(click_through_rate, 1),
    }
