"""Home feed personalization service."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func, select, or_
from sqlalchemy.orm import Session

from app.models import (
    Product,
    User,
    UserBehaviorEvent,
    WishlistItem,
)
from app.services.enhanced_recommendation_service import (
    get_recommendations,
    get_trending_products,
)
from app.controllers.catalog_controller import serialize_catalog_product


async def get_home_feed(db: Session, user: User | None) -> dict:
    """Build personalized home feed for user."""

    sections = {}

    # Section 1: Recommended for you (personalized or best-sellers)
    if user:
        recommendations = await get_recommendations(db, user, limit=12)
        sections["recommended"] = {
            "title": "Recommended for you",
            "subtitle": "Based on your preferences",
            "products": [
                {
                    "id": rec.product_id,
                    "reason": rec.reason,
                }
                for rec in recommendations[:6]
            ],
        }
    else:
        # Show best-sellers for anonymous users
        best_sellers = db.execute(
            select(
                Product.id,
                func.count(UserBehaviorEvent.id).label("view_count"),
            )
            .join(
                UserBehaviorEvent,
                UserBehaviorEvent.product_id == Product.id,
            )
            .where(
                Product.status == "active",
                Product.stock > 0,
                UserBehaviorEvent.event_type.in_(["product_view", "purchase"]),
                UserBehaviorEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
            )
            .group_by(Product.id)
            .order_by(func.count(UserBehaviorEvent.id).desc())
            .limit(6)
        ).all()

        sections["recommended"] = {
            "title": "Popular picks",
            "subtitle": "Trending with customers",
            "products": [{"id": product_id, "reason": "Popular"} for product_id, _ in best_sellers],
        }

    # Section 2: Trending now (last 7 days)
    trending = get_trending_products(db, days=7, limit=6)
    trending_product_ids = list(trending["scores"].keys())[:6]

    sections["trending"] = {
        "title": "Trending now",
        "subtitle": "What's popular this week",
        "products": [{"id": pid, "reason": "Trending"} for pid in trending_product_ids],
    }

    # Section 3: Recently viewed (if user is logged in)
    if user:
        recently_viewed = db.execute(
            select(UserBehaviorEvent.product_id)
            .where(
                UserBehaviorEvent.user_id == user.id,
                UserBehaviorEvent.event_type == "product_view",
                UserBehaviorEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
            )
            .distinct()
            .order_by(UserBehaviorEvent.created_at.desc())
            .limit(6)
        ).scalars().all()

        if recently_viewed:
            sections["recently_viewed"] = {
                "title": "Recently viewed",
                "subtitle": "Items you've browsed",
                "products": [{"id": pid, "reason": "Recently viewed"} for pid in recently_viewed],
            }

    # Section 4: Wishlist summary (if user has wishlisted items)
    if user:
        wishlist_count = db.scalar(
            select(func.count(WishlistItem.id)).where(WishlistItem.user_id == user.id)
        ) or 0

        if wishlist_count > 0:
            wishlist_items = db.execute(
                select(WishlistItem.product_id)
                .where(WishlistItem.user_id == user.id)
                .order_by(WishlistItem.created_at.desc())
                .limit(3)
            ).scalars().all()

            sections["wishlist"] = {
                "title": f"Your wishlist ({wishlist_count})",
                "subtitle": "Items you've saved",
                "count": wishlist_count,
                "products": [{"id": pid, "reason": "Saved"} for pid in wishlist_items],
            }

    # Section 5: Category spotlight (best performing category)
    category_performance = db.execute(
        select(
            Product.category,
            func.count(UserBehaviorEvent.id).label("interaction_count"),
            func.count(func.distinct(UserBehaviorEvent.user_id)).label("unique_users"),
        )
        .join(UserBehaviorEvent, UserBehaviorEvent.product_id == Product.id)
        .where(
            Product.status == "active",
            Product.stock > 0,
            UserBehaviorEvent.event_type.in_(["product_view", "purchase", "add_to_cart"]),
            UserBehaviorEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        )
        .group_by(Product.category)
        .order_by(func.count(UserBehaviorEvent.id).desc())
        .limit(1)
    ).first()

    if category_performance:
        category, _, _ = category_performance
        if category:
            category_products = db.execute(
                select(Product.id)
                .where(
                    Product.category == category,
                    Product.status == "active",
                    Product.stock > 0,
                )
                .order_by(func.random())
                .limit(6)
            ).scalars().all()

            if category_products:
                sections["category_spotlight"] = {
                    "title": f"Shop {category}",
                    "subtitle": "Customer favorite category",
                    "category": category,
                    "products": [{"id": pid, "reason": "Category pick"} for pid in category_products],
                }

    return {
        "sections": sections,
        "personalized": user is not None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_feed_section_products(
    db: Session,
    section_key: str,
    product_ids: list[str],
    limit: int = 12,
) -> list[dict]:
    """Get full product details for a feed section."""
    products = db.execute(
        select(Product)
        .where(
            Product.id.in_(product_ids[:limit]),
            Product.status == "active",
            Product.stock > 0,
        )
    ).all()

    # Maintain original order
    product_map = {p.id: serialize_catalog_product(db, p) for p, in products}
    return [product_map[pid] for pid in product_ids[:limit] if pid in product_map]
