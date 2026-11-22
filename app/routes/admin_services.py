"""Admin endpoints for the services marketplace.

Feature-banded like the rest of the admin surface: catalogue work sits
behind `catalog`, money and disputes behind `finance`, provider verification
behind `vendors`. A support admin can see the marketplace without being able
to release funds.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.controllers.admin.services import (
    create_category,
    deactivate_category,
    list_all_categories,
    list_bookings_for_admin,
    list_services_for_admin,
    resolve_dispute,
    services_marketplace_overview,
    set_provider_verification,
    set_service_admin_status,
    set_store_offerings,
    update_category,
)
from app.core.admin_permissions import require_admin_feature
from app.core.database import get_db
from app.models import User
from app.schemas.service import (
    BookingRead,
    ServiceCategoryRead,
    ServiceCategoryWrite,
    ServiceDetailRead,
)

router = APIRouter(prefix="/admin/services", tags=["admin-services"])

# Was require_admin_feature("catalog") — a feature name that appeared in no
# permission band, so every route below was reachable only by a super admin and
# every other administrator got a 403. "services" is a real module in the
# permission catalogue and in the bands, which is what was intended.
RequireServicesAdmin = Annotated[User, Depends(require_admin_feature("services"))]
RequireFinanceAdmin = Annotated[User, Depends(require_admin_feature("finance"))]
RequireVendorsAdmin = Annotated[User, Depends(require_admin_feature("vendors"))]


# --- Overview -------------------------------------------------------------

@router.get("/overview")
def get_overview(
    _: RequireServicesAdmin,
    window_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return services_marketplace_overview(db, window_days=window_days)


# --- Categories -----------------------------------------------------------

@router.get("/categories", response_model=list[ServiceCategoryRead])
def get_categories(_: RequireServicesAdmin, db: Session = Depends(get_db)):
    return list_all_categories(db)


@router.post(
    "/categories",
    response_model=ServiceCategoryRead,
    status_code=status.HTTP_201_CREATED,
)
def post_category(
    payload: ServiceCategoryWrite,
    _: RequireServicesAdmin,
    db: Session = Depends(get_db),
):
    return create_category(db, payload)


@router.patch("/categories/{category_id}", response_model=ServiceCategoryRead)
def patch_category(
    category_id: str,
    payload: ServiceCategoryWrite,
    _: RequireServicesAdmin,
    db: Session = Depends(get_db),
):
    return update_category(db, category_id, payload)


@router.delete("/categories/{category_id}", response_model=ServiceCategoryRead)
def delete_category(
    category_id: str,
    _: RequireServicesAdmin,
    db: Session = Depends(get_db),
):
    """Deactivates rather than deletes, so existing listings keep their home."""
    return deactivate_category(db, category_id)


# --- Listing moderation ---------------------------------------------------

@router.get("/listings", response_model=list[ServiceDetailRead])
def get_listings(
    _: RequireServicesAdmin,
    status_filter: str | None = Query(default=None, alias="status"),
    store_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return list_services_for_admin(
        db, status_filter=status_filter, store_id=store_id, limit=limit, offset=offset
    )


@router.post("/listings/{service_id}/status", response_model=ServiceDetailRead)
def post_listing_status(
    service_id: str,
    _: RequireServicesAdmin,
    new_status: Annotated[str, Query(alias="status")],
    reason: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
):
    """Approve into the catalogue, reject with a reason, or suspend."""
    return set_service_admin_status(db, service_id, new_status, reason=reason)


# --- Providers ------------------------------------------------------------

@router.post("/providers/{store_id}/verification")
def post_provider_verification(
    store_id: str,
    _: RequireVendorsAdmin,
    verification: Annotated[str, Query()],
    db: Session = Depends(get_db),
):
    store = set_provider_verification(db, store_id, verification)
    return {"store_id": store.id, "provider_verification": store.provider_verification}


@router.post("/providers/{store_id}/offerings")
def post_store_offerings(
    store_id: str,
    _: RequireVendorsAdmin,
    offerings: Annotated[list[str], Body(embed=True)],
    db: Session = Depends(get_db),
):
    """Switch products and/or services on for a store."""
    store = set_store_offerings(db, store_id, offerings)
    return {"store_id": store.id, "offerings": store.offerings}


# --- Bookings and disputes ------------------------------------------------

@router.get("/bookings", response_model=list[BookingRead])
def get_bookings(
    _: RequireFinanceAdmin,
    status_filter: str | None = Query(default=None, alias="status"),
    store_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return list_bookings_for_admin(
        db, status_filter=status_filter, store_id=store_id, limit=limit, offset=offset
    )


@router.post("/bookings/{booking_id}/resolve-dispute", response_model=BookingRead)
def post_resolve_dispute(
    booking_id: uuid.UUID,
    current_user: RequireFinanceAdmin,
    outcome: Annotated[str, Query()],
    note: Annotated[str, Query(max_length=500)],
    db: Session = Depends(get_db),
):
    """Settle a dispute. `completed` releases the hold; `cancelled` keeps it
    in treasury for the existing refund process."""
    return resolve_dispute(
        db, booking_id, outcome=outcome, note=note, admin_user=current_user
    )
