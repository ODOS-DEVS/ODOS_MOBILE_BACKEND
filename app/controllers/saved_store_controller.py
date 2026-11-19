"""Stores a shopper kept, and whether a store is verified.

Saved stores live against the account rather than the browser: a save has to
survive logout and follow the shopper to another device, which a localStorage
list cannot do.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SavedStore, Store, User
from app.models.user import VendorStatus
from app.schemas.catalog import SavedStoreRead, StoreRead

#: A store is browsable by the public only in these states. A suspended or
#: archived shop must not become reachable because somebody has its URL, and it
#: must not be savable either.
PUBLIC_STORE_STATUSES = frozenset({"active"})


def store_is_public(store: Store) -> bool:
    return store.status in PUBLIC_STORE_STATUSES


def verified_store_ids(db: Session, store_ids: list[str]) -> set[str]:
    """Which of these stores belong to an approved, unsuspended vendor.

    Verification is the vendor's own approval status, not the store row: the
    badge claims the *business* passed ODOS's checks, and that decision lives on
    the vendor application. A pending, rejected or suspended vendor is excluded
    by the same `vendor_status == approved` test the vendor APIs enforce, so a
    revoked vendor loses the badge the moment their status changes — there is no
    separate flag that could drift out of step.

    Batched deliberately: resolving this per store would put a query behind
    every card in a store list.
    """
    if not store_ids:
        return set()
    rows = db.execute(
        select(Store.id)
        .join(User, Store.vendor_user_id == User.id)
        .where(
            Store.id.in_(store_ids),
            User.vendor_status == VendorStatus.APPROVED,
        )
    ).all()
    return {row[0] for row in rows}


def _as_store_read(store: Store, *, verified: bool) -> StoreRead:
    """Serialize with the badge attached.

    Mirrors `annotate_store_verification`, which the catalog routes use — the
    flag is assigned onto the instance so `model_validate` picks it up with
    everything else rather than being threaded through by hand.
    """
    store.is_verified = verified
    return StoreRead.model_validate(store)


def _store_or_404(db: Session, store_id: str) -> Store:
    store = db.get(Store, store_id)
    if not store or not store_is_public(store):
        # Suspended and unpublished shops report as missing rather than as
        # forbidden, so this cannot be used to enumerate them.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store was not found.",
        )
    return store


def list_saved_stores(db: Session, user: User) -> list[SavedStoreRead]:
    """Newest first. Scoped to the caller — the route takes no user id."""
    rows = list(
        db.scalars(
            select(SavedStore)
            .where(SavedStore.user_id == user.id)
            .order_by(SavedStore.created_at.desc())
        ).all()
    )
    if not rows:
        return []

    stores = {
        store.id: store
        for store in db.scalars(
            select(Store).where(Store.id.in_([row.store_id for row in rows]))
        ).all()
    }
    verified = verified_store_ids(db, list(stores))

    payload: list[SavedStoreRead] = []
    for row in rows:
        store = stores.get(row.store_id)
        # A shop that was suspended or deleted after being saved drops out of
        # the list rather than rendering as a dead card.
        if not store or not store_is_public(store):
            continue
        payload.append(
            SavedStoreRead(
                store=_as_store_read(store, verified=store.id in verified),
                saved_at=row.created_at,
            )
        )
    return payload


def saved_store_ids(db: Session, user: User) -> set[str]:
    return set(
        db.scalars(select(SavedStore.store_id).where(SavedStore.user_id == user.id)).all()
    )


def save_store(db: Session, user: User, store_id: str) -> SavedStoreRead:
    """Idempotent: saving an already-saved store returns the existing row."""
    store = _store_or_404(db, store_id)

    existing = db.scalar(
        select(SavedStore).where(
            SavedStore.user_id == user.id,
            SavedStore.store_id == store.id,
        )
    )
    if existing:
        verified = verified_store_ids(db, [store.id])
        return SavedStoreRead(
            store=_as_store_read(store, verified=store.id in verified),
            saved_at=existing.created_at,
        )

    row = SavedStore(id=uuid.uuid4(), user_id=user.id, store_id=store.id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two taps racing each other. The unique constraint settled it; read
        # back whichever insert won rather than surfacing a 500.
        db.rollback()
        row = db.scalar(
            select(SavedStore).where(
                SavedStore.user_id == user.id,
                SavedStore.store_id == store.id,
            )
        )
        if row is None:
            raise
    else:
        db.refresh(row)

    verified = verified_store_ids(db, [store.id])
    return SavedStoreRead(
        store=_as_store_read(store, verified=store.id in verified),
        saved_at=row.created_at,
    )


def unsave_store(db: Session, user: User, store_id: str) -> None:
    """Removing something not saved is success, not a 404 — the end state is the same."""
    row = db.scalar(
        select(SavedStore).where(
            SavedStore.user_id == user.id,
            # Scoped to the caller: a user id from the browser is never trusted,
            # so one shopper cannot delete another's saved store.
            SavedStore.store_id == store_id,
        )
    )
    if row:
        db.delete(row)
        db.commit()
