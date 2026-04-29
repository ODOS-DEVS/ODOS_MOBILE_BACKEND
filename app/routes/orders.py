from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.controllers.order_controller import (
    cancel_order,
    confirm_order_delivery,
    create_order,
    delete_order,
    get_order,
    list_orders,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import MessageResponse
from app.schemas.order import OrderCreate, OrderRead

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderRead])
def get_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_orders(db, current_user)


@router.get("/{order_id}", response_model=OrderRead)
def get_order_by_id(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_order(db, current_user, order_id)


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_new_order(
    payload: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_order(db, current_user, payload)


@router.patch("/{order_id}/cancel", response_model=OrderRead)
def cancel_existing_order(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return cancel_order(db, current_user, order_id)


@router.patch("/{order_id}/deliver", response_model=OrderRead)
def confirm_existing_order_delivery(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return confirm_order_delivery(db, current_user, order_id)


@router.delete("/{order_id}", response_model=MessageResponse)
def delete_existing_order(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_order(db, current_user, order_id)
    return MessageResponse(message="Order removed successfully.")
