from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.controllers.wishlist_controller import (
    add_wishlist_item,
    list_wishlist_items,
    remove_wishlist_item,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import WishlistItemCreate, WishlistItemRead

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.get("", response_model=list[WishlistItemRead])
def get_wishlist(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_wishlist_items(db, current_user)


@router.post("", response_model=WishlistItemRead, status_code=status.HTTP_201_CREATED)
def create_wishlist_item(
    payload: WishlistItemCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return add_wishlist_item(db, current_user, payload)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wishlist_item(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    remove_wishlist_item(db, current_user, product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
