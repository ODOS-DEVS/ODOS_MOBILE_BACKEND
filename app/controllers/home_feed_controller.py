"""Home feed controller for personalized content."""

from sqlalchemy.orm import Session

from app.models import User
from app.services.home_feed_service import get_home_feed, get_feed_section_products


async def get_home_feed_structure(
    db: Session,
    current_user: User | None,
) -> dict:
    """Get home feed structure with section metadata."""
    return await get_home_feed(db, current_user)


async def get_home_feed_full(
    db: Session,
    current_user: User | None,
    limit_per_section: int = 12,
) -> dict:
    """Get complete home feed with full product details."""
    feed_structure = await get_home_feed(db, current_user)

    # Fetch full product details for each section
    sections_with_products = {}

    for section_key, section in feed_structure["sections"].items():
        product_ids = [p["id"] for p in section.get("products", [])]

        if product_ids:
            products = await get_feed_section_products(
                db,
                section_key,
                product_ids,
                limit=limit_per_section,
            )

            sections_with_products[section_key] = {
                "title": section["title"],
                "subtitle": section.get("subtitle"),
                "category": section.get("category"),
                "count": section.get("count"),
                "products": products,
            }

    return {
        "sections": sections_with_products,
        "personalized": feed_structure["personalized"],
        "generated_at": feed_structure["generated_at"],
    }


async def get_feed_section(
    db: Session,
    current_user: User | None,
    section_key: str,
    limit: int = 30,
    offset: int = 0,
) -> dict:
    """Get products from a specific feed section."""
    feed_structure = await get_home_feed(db, current_user)

    if section_key not in feed_structure["sections"]:
        return {
            "error": "Section not found",
            "available_sections": list(feed_structure["sections"].keys()),
        }

    section = feed_structure["sections"][section_key]
    product_ids = [p["id"] for p in section.get("products", [])]

    # Apply offset and limit
    paginated_ids = product_ids[offset : offset + limit]

    products = await get_feed_section_products(
        db,
        section_key,
        paginated_ids,
        limit=limit,
    )

    return {
        "section": section_key,
        "title": section["title"],
        "subtitle": section.get("subtitle"),
        "products": products,
        "total_count": len(product_ids),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < len(product_ids),
    }
