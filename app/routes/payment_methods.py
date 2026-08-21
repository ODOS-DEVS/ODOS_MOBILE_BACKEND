"""Payment methods API routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.payment_methods_controller import (
    get_available_payment_methods,
    initiate_payment,
    verify_payment_status,
)
from app.models import User

router = APIRouter(prefix="/payment-methods", tags=["payment-methods"])


@router.get("/available")
def list_available_payment_methods(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get list of available payment methods."""
    return get_available_payment_methods(db, current_user)


@router.post("/initiate")
async def initiate_payment_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    order_id: str = Query(..., min_length=1, max_length=100),
    provider: str = Query(..., min_length=1, max_length=50),
    phone_number: str | None = Query(default=None, max_length=20),
):
    """Initiate payment through specified provider."""
    return await initiate_payment(
        db,
        current_user,
        order_id,
        provider,
        phone_number=phone_number,
    )


@router.post("/verify")
async def verify_payment_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    order_id: str = Query(..., min_length=1, max_length=100),
    provider: str = Query(..., min_length=1, max_length=50),
    provider_reference: str | None = Query(default=None, max_length=200),
):
    """Verify payment status."""
    return await verify_payment_status(
        db,
        current_user,
        order_id,
        provider,
        provider_reference=provider_reference,
    )
