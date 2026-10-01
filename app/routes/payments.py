from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.controllers.payment_controller import (
    create_checkout_session,
    create_kutanapay_checkout_session,
    handle_kutanapay_webhook,
    handle_paystack_webhook,
    paystack_checkout_redirect,
    verify_checkout_session,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.rate_limit import limit_payment_checkout
from app.models import User
from app.schemas.payment import (
    CheckoutSessionCreate,
    CheckoutSessionRead,
    PaymentVerificationRead,
)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/checkout", response_model=CheckoutSessionRead)
def initialize_checkout_payment(
    request: Request,
    payload: CheckoutSessionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_payment_checkout(current_user)
    return create_checkout_session(db, request, current_user, payload)


@router.post("/checkout/{reference}/verify", response_model=PaymentVerificationRead)
def verify_checkout_payment(
    reference: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return verify_checkout_session(db, current_user, reference)


@router.post("/paystack/webhook")
async def receive_paystack_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_paystack_signature: str | None = Header(default=None),
):
    raw_body = await request.body()
    return handle_paystack_webhook(
        db,
        raw_body=raw_body,
        signature=x_paystack_signature,
    )


@router.get("/paystack/redirect", name="paystack_checkout_redirect")
def receive_paystack_redirect(
    request: Request,
    return_url: str,
):
    return paystack_checkout_redirect(request, return_url=return_url)


@router.post("/kutanapay/checkout", response_model=CheckoutSessionRead)
def initialize_kutanapay_checkout(
    request: Request,
    payload: CheckoutSessionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_payment_checkout(current_user)
    return create_kutanapay_checkout_session(db, request, current_user, payload)


@router.post("/kutanapay/webhook", name="kutanapay_webhook")
async def receive_kutanapay_webhook(
    request: Request,
    x_webhook_signature: Annotated[str | None, Header()] = None,
    x_webhook_event: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """KutanaPay's signed callback.

    The raw bytes are read before any parsing, because the signature is an HMAC
    over exactly what was sent -- re-serialising parsed JSON changes key order
    and whitespace and the comparison then always fails.
    """
    raw_body = await request.body()
    return handle_kutanapay_webhook(
        db,
        raw_body=raw_body,
        signature=x_webhook_signature,
        event_type=x_webhook_event,
    )
