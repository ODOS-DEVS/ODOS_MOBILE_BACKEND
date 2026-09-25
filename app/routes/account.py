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
from app.controllers.account_deletion_controller import (
    AccountDeletionEligibility,
    AccountDeletionRequest,
    delete_own_account,
    get_account_deletion_eligibility,
)
from app.controllers.email_preferences_controller import (
    EmailPreferencesUpdate,
    get_email_preferences,
    update_email_preferences,
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


@router.get("/email-preferences")
def get_preferences(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Get email preferences for current user."""
    return get_email_preferences(db, current_user)


@router.patch("/email-preferences")
def update_preferences(
    payload: EmailPreferencesUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Update email preferences for current user."""
    return update_email_preferences(db, current_user, payload)


@router.get("/deletion-eligibility", response_model=AccountDeletionEligibility)
def read_account_deletion_eligibility(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What stands between this account and deletion, if anything."""
    return get_account_deletion_eligibility(db, current_user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_account_endpoint(
    payload: AccountDeletionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete the signed-in user's own account.

    Required by App Store Guideline 5.1.1(v): an app that creates accounts must
    let people delete theirs from inside the app.
    """
    delete_own_account(db, current_user, payload)
