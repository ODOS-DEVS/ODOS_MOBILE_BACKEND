from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.controllers.customer_wallet_controller import (
    create_wallet_checkout,
    fetch_customer_wallet,
    initialize_wallet_topup,
    verify_wallet_topup,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.customer_wallet import (
    CustomerWalletRead,
    CustomerWalletTopUpCreate,
    CustomerWalletTopUpSessionRead,
    CustomerWalletTopUpVerificationRead,
    WalletCheckoutCreate,
    WalletCheckoutRead,
)

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/customer", response_model=CustomerWalletRead)
def get_customer_wallet(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_customer_wallet(db, current_user)


@router.post("/customer/topups/checkout", response_model=CustomerWalletTopUpSessionRead)
def create_customer_wallet_topup_session(
    request: Request,
    payload: CustomerWalletTopUpCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return initialize_wallet_topup(db, request, current_user, payload)


@router.post("/customer/topups/{reference}/verify", response_model=CustomerWalletTopUpVerificationRead)
def verify_customer_wallet_topup(
    reference: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return verify_wallet_topup(db, current_user, reference)


@router.post("/customer/checkout", response_model=WalletCheckoutRead)
def checkout_with_customer_wallet(
    payload: WalletCheckoutCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_wallet_checkout(db, current_user, payload)
