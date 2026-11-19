"""Stores a shopper kept.

Every route is scoped to the authenticated caller. None of them takes a user
identifier, so there is nothing a browser could change to read or delete another
shopper's saved stores.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.saved_store_controller import (
    list_saved_stores,
    save_store,
    unsave_store,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.catalog import SavedStoreRead

router = APIRouter(prefix="/saved-stores", tags=["saved-stores"])


@router.get("", response_model=list[SavedStoreRead])
def get_saved_stores(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Newest first. Shops that were suspended or removed after being saved are
    dropped rather than returned as dead cards."""
    return list_saved_stores(db, current_user)


@router.post("/{store_id}", response_model=SavedStoreRead, status_code=status.HTTP_201_CREATED)
def post_saved_store(
    store_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Idempotent — saving an already-saved store returns the existing save."""
    return save_store(db, current_user, store_id)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_store(
    store_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Removing something that is not saved succeeds: the end state is the same."""
    unsave_store(db, current_user, store_id)
