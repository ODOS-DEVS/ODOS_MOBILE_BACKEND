import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category


def slugify_taxonomy_value(value: str) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    return cleaned.strip("-")


def _match_subcategory_label(
    subcategories: list[str] | None,
    subcategory: str | None,
) -> str | None:
    if not subcategory or not subcategories:
        return subcategory

    normalized_target = slugify_taxonomy_value(subcategory)
    for label in subcategories:
        if slugify_taxonomy_value(label) == normalized_target:
            return label
        if label.strip().lower() == subcategory.strip().lower():
            return label
    return subcategory.strip()


def _find_active_category(
    db: Session,
    *,
    category: str,
    category_slug: str | None,
) -> Category | None:
    if category_slug:
        match = db.scalar(
            select(Category).where(
                Category.slug == category_slug.strip(),
                Category.is_active.is_(True),
            )
        )
        if match:
            return match

    normalized_category = slugify_taxonomy_value(category)
    categories = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order.asc(), Category.title.asc())
    ).all()

    for entry in categories:
        if entry.slug == normalized_category:
            return entry
        if slugify_taxonomy_value(entry.title) == normalized_category:
            return entry
        if entry.title.strip().lower() == category.strip().lower():
            return entry

    return None


def resolve_product_taxonomy(
    db: Session,
    *,
    category: str,
    subcategory: str | None = None,
    category_slug: str | None = None,
) -> tuple[str, str | None, list[str] | None, list[str] | None]:
    cleaned_category = category.strip()
    matched = _find_active_category(
        db,
        category=cleaned_category,
        category_slug=category_slug,
    )

    if matched:
        resolved_subcategory = _match_subcategory_label(
            matched.subcategories,
            subcategory,
        )
        category_slugs = [matched.slug]
        subcategory_slugs = (
            [slugify_taxonomy_value(resolved_subcategory)]
            if resolved_subcategory
            else None
        )
        return (
            matched.title,
            resolved_subcategory,
            category_slugs,
            subcategory_slugs,
        )

    fallback_slug = slugify_taxonomy_value(category_slug or cleaned_category)
    category_slugs = [fallback_slug] if fallback_slug else None
    subcategory_slugs = (
        [slugify_taxonomy_value(subcategory)] if subcategory else None
    )
    return (
        cleaned_category,
        subcategory.strip() if subcategory else None,
        category_slugs,
        subcategory_slugs,
    )
