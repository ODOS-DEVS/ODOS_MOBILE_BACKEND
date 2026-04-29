from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.account_controller import (
    create_address,
    create_payment_method,
    delete_address,
    delete_payment_method,
    list_addresses,
    list_payment_methods,
    set_default_address,
    set_default_payment_method,
    update_address,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.account import AddressCreate, AddressRead, AddressUpdate, PaymentMethodCreate, PaymentMethodRead
from app.schemas.user import MessageResponse

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/addresses", response_model=list[AddressRead])
def get_saved_addresses(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_addresses(db, current_user)


@router.post("/addresses", response_model=AddressRead, status_code=status.HTTP_201_CREATED)
def create_saved_address(
    payload: AddressCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_address(db, current_user, payload)


@router.patch("/addresses/{address_id}", response_model=AddressRead)
def update_saved_address(
    address_id: str,
    payload: AddressUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_address(db, current_user, address_id, payload)


@router.post("/addresses/{address_id}/default", response_model=AddressRead)
def make_saved_address_default(
    address_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return set_default_address(db, current_user, address_id)


@router.delete("/addresses/{address_id}", response_model=MessageResponse)
def delete_saved_address(
    address_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_address(db, current_user, address_id)
    return MessageResponse(message="Address removed successfully.")


@router.get("/payment-methods", response_model=list[PaymentMethodRead])
def get_saved_payment_methods(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_payment_methods(db, current_user)


@router.post("/payment-methods", response_model=PaymentMethodRead, status_code=status.HTTP_201_CREATED)
def create_saved_payment_method(
    payload: PaymentMethodCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_payment_method(db, current_user, payload)


@router.post("/payment-methods/{payment_method_id}/default", response_model=PaymentMethodRead)
def make_saved_payment_method_default(
    payment_method_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return set_default_payment_method(db, current_user, payment_method_id)


@router.delete("/payment-methods/{payment_method_id}", response_model=MessageResponse)
def delete_saved_payment_method(
    payment_method_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_payment_method(db, current_user, payment_method_id)
    return MessageResponse(message="Payment method removed successfully.")
