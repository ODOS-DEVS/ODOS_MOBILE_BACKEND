from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.courier_controller import (
    claim_delivery_offer,
    create_courier_profile,
    fetch_courier_profile,
    list_delivery_pool,
    update_courier_location,
    update_courier_status,
)
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models import User
from app.schemas.courier import (
    CourierLocationUpdate,
    CourierProfileCreate,
    CourierProfileRead,
    CourierStatusUpdate,
    DeliveryOfferRead,
)

router = APIRouter(prefix="/courier", tags=["courier"])


@router.get("/me", response_model=CourierProfileRead)
def get_my_courier_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_courier_profile(db, current_user)


@router.post("/profile", response_model=CourierProfileRead)
def post_courier_profile(
    payload: CourierProfileCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_courier_profile(db, current_user, payload)


@router.patch("/status", response_model=CourierProfileRead)
def patch_courier_status(
    payload: CourierStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_courier_status(db, current_user, payload)


@router.patch("/location", status_code=204)
def patch_courier_location(
    payload: CourierLocationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    update_courier_location(db, current_user, payload)


@router.get("/pool", response_model=list[DeliveryOfferRead])
def get_delivery_pool(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_delivery_pool(db, current_user)


@router.post("/pool/{offer_id}/claim", response_model=DeliveryOfferRead)
def post_claim_delivery_offer(
    offer_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return claim_delivery_offer(db, current_user, offer_id)
