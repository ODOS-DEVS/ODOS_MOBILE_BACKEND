from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.controllers.cart_controller import (
    add_cart_item,
    clear_cart_items,
    list_cart_items,
    remove_cart_item,
    update_cart_item_quantity,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import CartItemCreate, CartItemRead, CartItemUpdate

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("", response_model=list[CartItemRead])
def get_cart(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_cart_items(db, current_user)


@router.post("", response_model=CartItemRead, status_code=status.HTTP_201_CREATED)
def create_cart_item(
    payload: CartItemCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return add_cart_item(db, current_user, payload)


@router.patch("/{product_id}", response_model=CartItemRead)
def patch_cart_item(
    product_id: str,
    payload: CartItemUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_cart_item_quantity(db, current_user, product_id, payload)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cart_item(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    remove_cart_item(db, current_user, product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_cart(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    clear_cart_items(db, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
