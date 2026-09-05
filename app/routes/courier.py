from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.courier_controller import (
    advance_delivery,
    claim_delivery_offer,
    create_courier_profile,
    fetch_active_delivery,
    fetch_courier_profile,
    fetch_delivery,
    list_delivery_history,
    list_delivery_pool,
    update_courier_location,
    update_courier_status,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.courier import (
    CourierLocationUpdate,
    CourierProfileCreate,
    CourierProfileRead,
    CourierStatusUpdate,
    DeliveryIntentRequest,
    DeliveryOfferRead,
    DeliveryRead,
    DeliveryTimelineRead,
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


@router.post("/pool/{offer_id}/claim", response_model=DeliveryRead)
def post_claim_delivery_offer(
    offer_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return claim_delivery_offer(db, current_user, offer_id)


@router.get("/deliveries/active", response_model=DeliveryRead | None)
def get_active_delivery(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_active_delivery(db, current_user)


@router.get("/deliveries/history", response_model=list[DeliveryRead])
def get_delivery_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    limit: int = 50,
):
    return list_delivery_history(db, current_user, limit=limit)


@router.get("/deliveries/{delivery_id}", response_model=DeliveryTimelineRead)
def get_delivery(
    delivery_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_delivery(db, current_user, delivery_id)


# One endpoint per intent rather than a single {intent} path parameter: the
# OpenAPI schema then lists exactly what a rider can do, and an intent the app
# is not allowed to trigger cannot be reached by editing a URL segment.
def _intent_route(path: str, intent: str, summary: str):
    @router.post(f"/deliveries/{{delivery_id}}/{path}", response_model=DeliveryRead, summary=summary)
    def handler(  # noqa: D401
        delivery_id: UUID,
        current_user: Annotated[User, Depends(get_current_user)],
        payload: DeliveryIntentRequest | None = None,
        db: Session = Depends(get_db),
        _intent: str = intent,
    ):
        return advance_delivery(db, current_user, delivery_id, _intent, payload)

    handler.__name__ = f"post_delivery_{intent}"
    return handler


_intent_route("start-pickup", "start_pickup", "Heading to the vendor")
_intent_route("arrive-at-pickup", "arrive_at_pickup", "Arrived at the vendor")
_intent_route(
    "begin-pickup-verification",
    "begin_pickup_verification",
    "Ready to verify the handover with the vendor",
)
_intent_route("start-dropoff", "start_dropoff", "Heading to the customer")
_intent_route("arrive-at-customer", "arrive_at_customer", "Arrived at the customer")
_intent_route(
    "begin-delivery-verification",
    "begin_delivery_verification",
    "Ready to take the customer's delivery code",
)
_intent_route(
    "report-customer-unavailable",
    "report_customer_unavailable",
    "Customer could not be reached",
)
_intent_route("fail-delivery", "fail_delivery", "Delivery could not be completed")
_intent_route("start-return", "start_return", "Taking the order back to the vendor")
_intent_route("release", "release", "Hand the delivery back to the pool")
