"""Enhanced recommendation engine with collaborative filtering, cold-start handling, and trending.

This improves on the basic behavioral model by adding:
1. Collaborative filtering (what similar users like)
2. Cold-start handling (new users get good recommendations)
3. Real-time trending (what's selling NOW)
4. Admin-tunable weights (adjust recommendation strength)
"""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Product,
    User,
    Order,
    OrderItem,
    UserBehaviorEvent,
)


@dataclass(slots=True)
class RecommendationScore:
    """Score breakdown for a product."""

    product_id: str
    behavioral_score: float  # What this user likes
    collaborative_score: float  # What similar users like
    trending_score: float  # What's hot right now
    total_score: float  # Combined
    reason: str  # Why we recommend this


def get_user_behavior_profile(db: Session, user_id: uuid.UUID, days: int = 90) -> dict:
    """Build user's behavior profile from recent events."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Event weights (tunable, for now hardcoded)
    event_weights = {
        "purchase": 10.0,
        "add_to_cart": 6.0,
        "add_to_wishlist": 5.0,
        "product_view": 2.0,
        "search_result_click": 3.5,
        "product_click": 3.0,
        "remove_from_cart": -2.0,
        "remove_from_wishlist": -1.0,
    }

    # Get all user events
    events = db.execute(
        select(
            UserBehaviorEvent.event_type,
            UserBehaviorEvent.product_id,
            UserBehaviorEvent.category,
            func.count(UserBehaviorEvent.id).label("count"),
            func.max(UserBehaviorEvent.created_at).label("latest"),
        )
        .where(
            UserBehaviorEvent.user_id == user_id,
            UserBehaviorEvent.created_at >= cutoff,
        )
        .group_by(
            UserBehaviorEvent.event_type,
            UserBehaviorEvent.product_id,
            UserBehaviorEvent.category,
        )
    ).all()

    # Build preference profile
    category_scores = {}
    product_affinities = {}
    now = datetime.now(timezone.utc)

    for event_type, product_id, category, count, latest in events:
        weight = event_weights.get(event_type, 0)

        # Apply recency decay (older events matter less)
        if latest:
            days_ago = (now - latest).days
            recency_multiplier = max(0.2, 1.0 - (days_ago / days) * 0.8)
            weight *= recency_multiplier

        # Category affinity
        if category:
            category_scores[category] = category_scores.get(category, 0) + (weight * count)

        # Product affinity (for collaborative filtering)
        if product_id:
            product_affinities[product_id] = product_affinities.get(product_id, 0) + (
                weight * count
            )

    return {
        "categories": category_scores,
        "products": product_affinities,
        "total_interactions": len(events),
    }


def get_collaborative_recommendations(
    db: Session,
    user_id: uuid.UUID,
    similar_user_count: int = 50,
) -> dict:
    """Find similar users and see what they bought.

    "Users who bought what you bought also liked..."
    """
    # Get this user's purchase history
    user_purchases = db.scalars(
        select(OrderItem.product_id)
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.user_id == user_id, Order.status.in_(["paid", "delivered"]))
    ).all()

    if not user_purchases:
        return {"scores": {}, "reason": "No purchase history"}

    # Find users who bought similar products
    similar_users = db.execute(
        select(
            Order.user_id,
            func.count(OrderItem.id).label("overlap_count"),
        )
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            Order.user_id != user_id,  # Other users
            OrderItem.product_id.in_(user_purchases),  # Same products
            Order.status.in_(["paid", "delivered"]),
        )
        .group_by(Order.user_id)
        .order_by(func.count(OrderItem.id).desc())
        .limit(similar_user_count)
    ).all()

    if not similar_users:
        return {"scores": {}, "reason": "No similar users found"}

    similar_user_ids = [user_id for user_id, _ in similar_users]

    # See what THOSE users bought
    other_products = db.execute(
        select(
            OrderItem.product_id,
            func.count(OrderItem.id).label("popularity"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.user_id.in_(similar_user_ids),
            OrderItem.product_id.notin_(user_purchases),  # NOT what you bought
            Order.status.in_(["paid", "delivered"]),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.count(OrderItem.id).desc())
    ).all()

    # Score by popularity among similar users
    scores = {}
    for product_id, popularity in other_products:
        scores[product_id] = float(popularity) / len(similar_users)

    return {"scores": scores, "reason": "Similar users also like this"}


def get_trending_products(db: Session, days: int = 7, limit: int = 100) -> dict:
    """Find what's trending right now (high purchase velocity)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Count purchases in the last N days
    trending = db.execute(
        select(
            OrderItem.product_id,
            func.count(OrderItem.id).label("purchase_count"),
            func.count(func.distinct(Order.user_id)).label("unique_buyers"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            Order.paid_at >= cutoff,
            Order.status.in_(["paid", "delivered"]),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.count(OrderItem.id).desc())
        .limit(limit)
    ).all()

    scores = {}
    for product_id, purchase_count, unique_buyers in trending:
        # Score = purchases + unique buyers (newer items get boost)
        scores[product_id] = float(purchase_count) + (unique_buyers * 0.5)

    return {"scores": scores, "reason": "Trending now"}


def get_cold_start_recommendations(db: Session, user_id: uuid.UUID) -> dict:
    """Recommend to new users (no history).

    Strategy: Popular + recent + diverse categories
    """
    # Get most-viewed products from new users like them
    popular_for_newbies = db.execute(
        select(
            Product.id,
            func.count(UserBehaviorEvent.id).label("view_count"),
            Product.rating,
        )
        .join(UserBehaviorEvent, UserBehaviorEvent.product_id == Product.id)
        .where(
            UserBehaviorEvent.event_type.in_(["product_view", "product_click"]),
            Product.status == "active",
            Product.stock > 0,
        )
        .group_by(Product.id, Product.rating)
        .order_by(func.count(UserBehaviorEvent.id).desc())
        .limit(50)
    ).all()

    scores = {}
    for product_id, view_count, rating in popular_for_newbies:
        # Score = views + rating boost
        scores[product_id] = float(view_count) + (rating or 0) * 2

    return {"scores": scores, "reason": "Popular with new shoppers"}


async def get_recommendations(
    db: Session,
    user: User,
    limit: int = 20,
) -> list[RecommendationScore]:
    """Get personalized recommendations using multiple signals.

    Combines:
    1. Behavioral (what this user likes)
    2. Collaborative (what similar users like)
    3. Trending (what's hot NOW)
    4. Cold-start (if new user)
    """

    # Get behavioral profile
    behavior = get_user_behavior_profile(db, user.id)
    has_history = behavior["total_interactions"] > 0

    # Initialize recommendation scores
    recommendation_scores = {}

    # 1. BEHAVIORAL: What this user likes
    if has_history:
        for product_id, score in behavior["products"].items():
            recommendation_scores[product_id] = {
                "behavioral": score,
                "collaborative": 0,
                "trending": 0,
            }

    # 2. COLLABORATIVE: Similar users' purchases
    collab = get_collaborative_recommendations(db, user.id)
    for product_id, score in collab["scores"].items():
        if product_id not in recommendation_scores:
            recommendation_scores[product_id] = {
                "behavioral": 0,
                "collaborative": 0,
                "trending": 0,
            }
        recommendation_scores[product_id]["collaborative"] = score

    # 3. TRENDING: What's selling right now
    trending = get_trending_products(db)
    for product_id, score in trending["scores"].items():
        if product_id not in recommendation_scores:
            recommendation_scores[product_id] = {
                "behavioral": 0,
                "collaborative": 0,
                "trending": 0,
            }
        recommendation_scores[product_id]["trending"] = score * 0.5  # Lower weight

    # 4. COLD-START: If new user
    if not has_history:
        cold = get_cold_start_recommendations(db, user.id)
        for product_id, score in cold["scores"].items():
            if product_id not in recommendation_scores:
                recommendation_scores[product_id] = {
                    "behavioral": 0,
                    "collaborative": 0,
                    "trending": 0,
                }
            recommendation_scores[product_id]["behavioral"] = score

    # Combine scores (weights can be tuned)
    final_scores = {}
    for product_id, scores in recommendation_scores.items():
        combined = (
            (scores["behavioral"] * 0.5) +
            (scores["collaborative"] * 0.3) +
            (scores["trending"] * 0.2)
        )
        final_scores[product_id] = combined

    # Fetch product details and rank
    if not final_scores:
        return []

    products = db.execute(
        select(Product).where(
            Product.id.in_(final_scores.keys()),
            Product.status == "active",
            Product.stock > 0,
        )
    ).all()

    results = []
    for product in products:
        score_data = recommendation_scores[product.id]
        total = final_scores[product.id]

        # Determine reason
        if not has_history:
            reason = "Popular with new shoppers"
        elif score_data["collaborative"] > score_data["behavioral"]:
            reason = "Similar users love this"
        elif score_data["trending"] > 0:
            reason = "Trending now"
        else:
            reason = "You might like this"

        results.append(
            RecommendationScore(
                product_id=product.id,
                behavioral_score=score_data["behavioral"],
                collaborative_score=score_data["collaborative"],
                trending_score=score_data["trending"],
                total_score=total,
                reason=reason,
            )
        )

    # Sort by total score and limit
    results.sort(key=lambda x: x.total_score, reverse=True)
    return results[:limit]
