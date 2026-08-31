"""Vendor-facing management of a shop's own product sections.

Every function resolves the caller's store through get_vendor_store rather than
trusting an id from the request. A vendor naming another shop's section must not
be able to rename or delete it — the same ownership mistake that made vendor
analytics query a column that did not exist.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.controllers.vendor_controller import get_vendor_store, require_vendor_access
from app.models import Product, Store, StoreSection, StoreSectionProduct, User
from app.schemas.vendor import (
    VendorStoreSectionCreate,
    VendorStoreSectionProductsUpdate,
    VendorStoreSectionRead,
    VendorStoreSectionReorder,
    VendorStoreSectionSuggestions,
    VendorStoreSectionUpdate,
)
from app.services.store_section_service import (
    list_sections,
    next_sort_order,
    product_counts,
    slugify_section,
    starter_sections_for_category,
)


def _require_store(db: Session, user: User) -> Store:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You do not have a store yet.",
        )
    return store


def _owned_section(db: Session, store: Store, section_id: uuid.UUID) -> StoreSection:
    section = db.scalar(
        select(StoreSection).where(
            StoreSection.id == section_id,
            # Scoped to the caller's store, so a section id belonging to another
            # shop reads as "not found" rather than being editable.
            StoreSection.store_id == store.id,
        )
    )
    if not section:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That section was not found in your store.",
        )
    return section


def _serialize(sections: list[StoreSection], counts: dict[uuid.UUID, int]):
    return [
        VendorStoreSectionRead(
            id=section.id,
            title=section.title,
            slug=section.slug,
            sort_order=section.sort_order,
            is_active=section.is_active,
            product_count=counts.get(section.id, 0),
        )
        for section in sections
    ]


def fetch_vendor_sections(db: Session, user: User) -> list[VendorStoreSectionRead]:
    store = _require_store(db, user)
    sections = list_sections(db, store.id)
    return _serialize(sections, product_counts(db, [s.id for s in sections]))


def fetch_starter_suggestions(db: Session, user: User) -> VendorStoreSectionSuggestions:
    store = _require_store(db, user)
    existing = {s.slug for s in list_sections(db, store.id)}
    suggested = starter_sections_for_category(store.category)
    # Do not re-suggest a shelf the vendor already has, or the empty state
    # offers duplicates that would each fail on the unique constraint.
    return VendorStoreSectionSuggestions(
        titles=[t for t in suggested if slugify_section(t) not in existing]
    )


def create_vendor_section(
    db: Session, user: User, payload: VendorStoreSectionCreate
) -> VendorStoreSectionRead:
    store = _require_store(db, user)
    slug = slugify_section(payload.title)

    existing = db.scalar(
        select(StoreSection).where(
            StoreSection.store_id == store.id, StoreSection.slug == slug
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'You already have a section called "{existing.title}".',
        )

    section = StoreSection(
        store_id=store.id,
        title=payload.title,
        slug=slug,
        sort_order=next_sort_order(db, store.id),
    )
    db.add(section)
    db.commit()
    db.refresh(section)
    return VendorStoreSectionRead(
        id=section.id,
        title=section.title,
        slug=section.slug,
        sort_order=section.sort_order,
        is_active=section.is_active,
        product_count=0,
    )


def update_vendor_section(
    db: Session,
    user: User,
    section_id: uuid.UUID,
    payload: VendorStoreSectionUpdate,
) -> VendorStoreSectionRead:
    store = _require_store(db, user)
    section = _owned_section(db, store, section_id)

    if payload.title is not None and payload.title != section.title:
        slug = slugify_section(payload.title)
        clash = db.scalar(
            select(StoreSection).where(
                StoreSection.store_id == store.id,
                StoreSection.slug == slug,
                StoreSection.id != section.id,
            )
        )
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'You already have a section called "{clash.title}".',
            )
        section.title = payload.title
        section.slug = slug

    if payload.is_active is not None:
        section.is_active = payload.is_active

    db.commit()
    db.refresh(section)
    counts = product_counts(db, [section.id])
    return _serialize([section], counts)[0]


def delete_vendor_section(db: Session, user: User, section_id: uuid.UUID) -> None:
    store = _require_store(db, user)
    section = _owned_section(db, store, section_id)
    # Removing a shelf unshelves its items; it never deletes products. The
    # cascade only clears the join rows.
    db.delete(section)
    db.commit()


def reorder_vendor_sections(
    db: Session, user: User, payload: VendorStoreSectionReorder
) -> list[VendorStoreSectionRead]:
    store = _require_store(db, user)
    sections = {s.id: s for s in list_sections(db, store.id)}

    unknown = [str(sid) for sid in payload.section_ids if sid not in sections]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That order includes a section that is not in your store.",
        )

    for position, section_id in enumerate(payload.section_ids, start=1):
        sections[section_id].sort_order = position

    db.commit()
    ordered = list_sections(db, store.id)
    return _serialize(ordered, product_counts(db, [s.id for s in ordered]))


def fetch_section_product_ids(
    db: Session, user: User, section_id: uuid.UUID
) -> list[str]:
    """Which products are on this shelf, from the vendor's point of view.

    Unlike the customer endpoint this does not hide out-of-stock or unapproved
    products: the picker has to show them ticked, or a vendor would re-add an
    item that is already there and wonder why nothing changed.
    """
    store = _require_store(db, user)
    section = _owned_section(db, store, section_id)
    return [
        str(pid)
        for pid in db.scalars(
            select(StoreSectionProduct.product_id)
            .where(StoreSectionProduct.section_id == section.id)
            .order_by(StoreSectionProduct.sort_order)
        ).all()
    ]


def add_products_to_section(
    db: Session,
    user: User,
    section_id: uuid.UUID,
    payload: VendorStoreSectionProductsUpdate,
) -> VendorStoreSectionRead:
    store = _require_store(db, user)
    section = _owned_section(db, store, section_id)

    # Only this store's products may be shelved here. Without this a vendor
    # could place a competitor's product on their own page.
    owned_ids = set(
        db.scalars(
            select(Product.id).where(
                Product.id.in_(payload.product_ids), Product.store_id == store.id
            )
        ).all()
    )
    rejected = [pid for pid in payload.product_ids if pid not in owned_ids]
    if rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Some of those products are not in your store.",
        )

    already = set(
        db.scalars(
            select(StoreSectionProduct.product_id).where(
                StoreSectionProduct.section_id == section.id
            )
        ).all()
    )
    position = int(
        db.scalar(
            select(func.max(StoreSectionProduct.sort_order)).where(
                StoreSectionProduct.section_id == section.id
            )
        )
        or 0
    )

    for product_id in payload.product_ids:
        # Re-adding a product already on the shelf is a no-op, not an error —
        # a vendor re-selecting it in the picker means "it belongs here".
        if product_id in already:
            continue
        position += 1
        db.add(
            StoreSectionProduct(
                section_id=section.id, product_id=product_id, sort_order=position
            )
        )

    db.commit()
    return _serialize([section], product_counts(db, [section.id]))[0]


def remove_product_from_section(
    db: Session, user: User, section_id: uuid.UUID, product_id: str
) -> VendorStoreSectionRead:
    store = _require_store(db, user)
    section = _owned_section(db, store, section_id)
    db.execute(
        delete(StoreSectionProduct).where(
            StoreSectionProduct.section_id == section.id,
            StoreSectionProduct.product_id == product_id,
        )
    )
    db.commit()
    return _serialize([section], product_counts(db, [section.id]))[0]
