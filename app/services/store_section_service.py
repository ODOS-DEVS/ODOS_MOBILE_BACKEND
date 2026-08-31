"""Per-store product sections — the shelves a shop arranges itself.

Separate from the platform Category taxonomy on purpose. Categories are shared
by every store so that cross-store browse works; sections belong to one shop and
are never used to filter across stores, because two shops with a "Kids" shelf do
not mean the same thing by it.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, StoreSection, StoreSectionProduct

# Offered to a vendor staring at an empty screen, keyed loosely on the store's
# own category. Suggestions, not a taxonomy: they are editable before saving and
# live here rather than in the database precisely so nobody has to maintain them
# as though they were authoritative.
STARTER_SECTIONS: dict[str, list[str]] = {
    "fashion": [
        "Shirts", "T-Shirts", "Trousers", "Jeans", "Shorts",
        "Hoodies", "Shoes", "Accessories",
    ],
    "books": [
        "Personal Development", "Fiction", "Comics", "Kids",
        "Religion", "Business & Forex", "Academic",
    ],
    "electronics": ["Phones", "Laptops", "Audio", "Accessories", "Chargers & Cables"],
    "beauty": ["Skincare", "Makeup", "Fragrance", "Hair", "Tools"],
    "groceries": ["Staples", "Drinks", "Snacks", "Household", "Fresh"],
}

FALLBACK_SECTIONS = ["New Arrivals", "Best Sellers", "Sale"]

# Matching is deliberately loose: a vendor types their own category text, so
# "Fashion & Style" and "fashion" should land on the same suggestions.
_CATEGORY_ALIASES: dict[str, str] = {
    "fashion": "fashion", "clothing": "fashion", "clothes": "fashion",
    "apparel": "fashion", "wear": "fashion", "boutique": "fashion",
    "book": "books", "books": "books", "bookshop": "books", "stationery": "books",
    "electronic": "electronics", "electronics": "electronics", "gadget": "electronics",
    "phone": "electronics", "tech": "electronics", "computer": "electronics",
    "beauty": "beauty", "cosmetic": "beauty", "skincare": "beauty",
    "makeup": "beauty", "salon": "beauty",
    "grocery": "groceries", "groceries": "groceries", "food": "groceries",
    "supermarket": "groceries", "provision": "groceries",
}


def slugify_section(title: str) -> str:
    """A stable, URL-safe key for a section title.

    Uniqueness is (store_id, slug), so this is what decides whether "T-Shirts"
    and "t shirts" are the same shelf. They should be — a vendor who types the
    second while the first exists means the one they already have.
    """
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return slug[:80] or "section"


def starter_sections_for_category(category: str | None) -> list[str]:
    """Suggested shelves for a shop, by what the shop sells."""
    if not category:
        return list(FALLBACK_SECTIONS)
    haystack = category.lower()
    for needle, key in _CATEGORY_ALIASES.items():
        if needle in haystack:
            return list(STARTER_SECTIONS[key])
    return list(FALLBACK_SECTIONS)


def list_sections(db: Session, store_id: str) -> list[StoreSection]:
    return list(
        db.scalars(
            select(StoreSection)
            .where(StoreSection.store_id == store_id)
            .order_by(StoreSection.sort_order, StoreSection.title)
        ).all()
    )


def product_counts(db: Session, section_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not section_ids:
        return {}
    rows = db.execute(
        select(
            StoreSectionProduct.section_id,
            func.count(StoreSectionProduct.product_id),
        )
        .where(StoreSectionProduct.section_id.in_(section_ids))
        .group_by(StoreSectionProduct.section_id)
    ).all()
    return {section_id: int(count) for section_id, count in rows}


def next_sort_order(db: Session, store_id: str) -> int:
    highest = db.scalar(
        select(func.max(StoreSection.sort_order)).where(
            StoreSection.store_id == store_id
        )
    )
    return int(highest or 0) + 1


def products_in_section(
    db: Session, section_id: uuid.UUID, *, visible_only: bool = False
) -> list[Product]:
    """Products on a shelf.

    visible_only drops anything a customer should not see, which is what makes
    "hide empty sections" mean *visibly* empty rather than merely unassigned —
    a shelf holding three out-of-stock items is empty to a shopper.
    """
    query = (
        select(Product)
        .join(StoreSectionProduct, StoreSectionProduct.product_id == Product.id)
        .where(StoreSectionProduct.section_id == section_id)
        .order_by(StoreSectionProduct.sort_order, Product.title)
    )
    if visible_only:
        query = query.where(Product.stock > 0)
    return list(db.scalars(query).all())
