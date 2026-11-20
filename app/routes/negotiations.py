"""Price negotiation endpoints.

Every route resolves the caller's role from the negotiation itself. None of
them accepts a user or vendor identifier, so there is nothing in a request that
could point an action at someone else's negotiation.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.controllers.negotiation_controller import (
    accept_offer,
    counter_offer,
    get_negotiation,
    list_negotiations,
    negotiation_counts,
    reject_offer,
    start_negotiation,
    withdraw_offer,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.negotiation import (
    NegotiationCountsRead,
    NegotiationOfferPayload,
    NegotiationRead,
    NegotiationRespondPayload,
    NegotiationStartPayload,
)

router = APIRouter(prefix="/negotiations", tags=["negotiations"])


@router.get("", response_model=list[NegotiationRead])
def get_my_negotiations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    role: str = Query(default="customer", pattern="^(customer|vendor)$"),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
):
    """The caller's own negotiations, as shopper or as vendor."""
    return list_negotiations(
        db, current_user, as_vendor=role == "vendor", status_filter=status_filter
    )


@router.get("/counts", response_model=NegotiationCountsRead)
def get_negotiation_counts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    role: str = Query(default="customer", pattern="^(customer|vendor)$"),
):
    return negotiation_counts(db, current_user, as_vendor=role == "vendor")


@router.post("", response_model=NegotiationRead, status_code=status.HTTP_201_CREATED)
def post_negotiation(
    payload: NegotiationStartPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Open a negotiation with an offer."""
    return start_negotiation(db, current_user, payload)


@router.get("/{negotiation_id}", response_model=NegotiationRead)
def get_one_negotiation(
    negotiation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_negotiation(db, current_user, negotiation_id)


@router.post("/{negotiation_id}/counter", response_model=NegotiationRead)
def post_counter_offer(
    negotiation_id: uuid.UUID,
    payload: NegotiationOfferPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return counter_offer(db, current_user, negotiation_id, payload)


@router.post("/{negotiation_id}/accept", response_model=NegotiationRead)
def post_accept_offer(
    negotiation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Accept the other side's pending offer, creating the agreement."""
    return accept_offer(db, current_user, negotiation_id)


@router.post("/{negotiation_id}/reject", response_model=NegotiationRead)
def post_reject_offer(
    negotiation_id: uuid.UUID,
    payload: NegotiationRespondPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return reject_offer(db, current_user, negotiation_id, payload)


@router.post("/{negotiation_id}/withdraw", response_model=NegotiationRead)
def post_withdraw_offer(
    negotiation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return withdraw_offer(db, current_user, negotiation_id)
