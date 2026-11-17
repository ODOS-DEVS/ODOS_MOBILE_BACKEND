"""Services marketplace endpoints.

Split into three routers so the access rules are visible in the URL rather
than buried per-handler: `/services` is public reading, `/bookings` is the
signed-in customer, `/vendor/services` is the provider's own catalogue.
"""

from datetime import date
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.controllers.booking_payment_controller import (
    create_booking_checkout,
    verify_booking_payment_for_user,
)
from app.controllers.service_controller import (
    accept_customer_quotation,
    add_availability_exception,
    create_quotation_request,
    decline_customer_quotation,
    list_my_quotations,
    list_provider_quotations,
    send_provider_quotation,
    confirm_booking_work,
    get_booking_hold,
    create_customer_booking,
    create_provider_service,
    create_service_review,
    get_booking,
    get_public_service,
    get_service_slots,
    list_categories,
    list_my_bookings,
    list_provider_bookings,
    list_provider_services,
    quote_service,
    list_availability_rules,
    replace_availability_rules,
    search_services,
    set_service_status,
    transition_booking,
    update_provider_service,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.service import (
    AvailabilityExceptionWrite,
    AvailabilityRuleRead,
    AvailabilityRuleWrite,
    BookingCreate,
    BookingDetailRead,
    BookingQuoteRead,
    BookingQuoteRequest,
    BookingRead,
    BookingTransitionRequest,
    QuotationAcceptWrite,
    QuotationRead,
    QuotationRequestWrite,
    QuotationSendWrite,
    ServiceCategoryRead,
    ServiceDetailRead,
    ServiceReviewRead,
    ServiceReviewWrite,
    ServiceSummaryRead,
    ServiceWrite,
    SlotRead,
)

router = APIRouter(prefix="/services", tags=["services"])
bookings_router = APIRouter(prefix="/bookings", tags=["bookings"])
vendor_router = APIRouter(prefix="/vendor/services", tags=["vendor-services"])
quotations_router = APIRouter(prefix="/quotations", tags=["quotations"])


# --- Public discovery -----------------------------------------------------

@router.get("/categories", response_model=list[ServiceCategoryRead])
def get_categories(db: Session = Depends(get_db)):
    return list_categories(db)


@router.get("", response_model=list[ServiceSummaryRead])
def get_services(
    q: str | None = Query(default=None, max_length=120),
    category_id: str | None = None,
    store_id: str | None = Query(default=None, max_length=100),
    city: str | None = Query(default=None, max_length=120),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    location_mode: str | None = None,
    booking_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return search_services(
        db,
        query=q,
        category_id=category_id,
        store_id=store_id,
        city=city,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        location_mode=location_mode,
        booking_type=booking_type,
        limit=limit,
        offset=offset,
    )


@router.get("/{service_id}", response_model=ServiceDetailRead)
def get_service(service_id: str, db: Session = Depends(get_db)):
    return get_public_service(db, service_id)


@router.get("/{service_id}/slots", response_model=list[SlotRead])
def get_slots(
    service_id: str,
    day: date,
    package_id: str | None = None,
    addon_ids: Annotated[list[str] | None, Query()] = None,
    hours: float | None = Query(default=None, gt=0, le=24),
    db: Session = Depends(get_db),
):
    return get_service_slots(
        db, service_id, day=day, package_id=package_id, addon_ids=addon_ids, hours=hours
    )


@router.post("/{service_id}/quote", response_model=BookingQuoteRead)
def post_quote(
    service_id: str,
    payload: BookingQuoteRequest,
    db: Session = Depends(get_db),
):
    """What a configuration costs. Advisory — the booking reprices on write."""
    return quote_service(db, service_id, payload)


@router.post(
    "/{service_id}/bookings",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
)
def post_booking(
    service_id: str,
    payload: BookingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_customer_booking(db, service_id, payload, current_user)


# --- Customer bookings ----------------------------------------------------

@bookings_router.get("", response_model=list[BookingRead])
def get_my_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_my_bookings(db, current_user, status_filter=status_filter, limit=limit)


@bookings_router.get("/{booking_id}", response_model=BookingDetailRead)
def get_one_booking(
    booking_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    booking, actor_role = get_booking(db, booking_id, current_user)
    detail = BookingDetailRead.model_validate(booking)
    if actor_role == "customer":
        # The provider's cut and the platform's commission are not the
        # customer's business, and showing them invites arguments about a
        # split they are not party to.
        detail.provider_net_amount = None
        detail.commission_amount = None
    return detail


@bookings_router.post("/{booking_id}/transition", response_model=BookingRead)
def post_transition(
    booking_id: uuid.UUID,
    payload: BookingTransitionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return transition_booking(db, booking_id, payload, current_user)


@bookings_router.post("/{booking_id}/checkout")
def post_booking_checkout(
    booking_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    payment_phone: str | None = Query(default=None, max_length=20),
    callback_url: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
):
    """Open a KutanaPay checkout for the full booking amount."""
    return create_booking_checkout(
        db,
        request,
        current_user,
        booking_id,
        payment_phone=payment_phone,
        callback_url=callback_url,
    )


@bookings_router.post("/payments/{reference}/verify")
def post_verify_booking_payment(
    reference: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Verify from the app's return screen. The gateway is the authority."""
    return verify_booking_payment_for_user(db, reference, current_user)


@bookings_router.post("/{booking_id}/confirm", response_model=BookingRead)
def post_confirm_work(
    booking_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Customer releases the held payment to the provider."""
    return confirm_booking_work(db, booking_id, current_user)


@bookings_router.get("/{booking_id}/hold")
def get_hold(
    booking_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """What Odos is holding on this booking, and when it releases."""
    return get_booking_hold(db, booking_id, current_user)


@bookings_router.post(
    "/{booking_id}/review",
    response_model=ServiceReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def post_review(
    booking_id: uuid.UUID,
    payload: ServiceReviewWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_service_review(db, booking_id, payload, current_user)


# --- Provider catalogue ---------------------------------------------------

@vendor_router.get("", response_model=list[ServiceSummaryRead])
def get_my_services(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_provider_services(db, current_user)


@vendor_router.post("", response_model=ServiceDetailRead, status_code=status.HTTP_201_CREATED)
def post_service(
    payload: ServiceWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_provider_service(db, payload, current_user)


@vendor_router.patch("/{service_id}", response_model=ServiceDetailRead)
def patch_service(
    service_id: str,
    payload: ServiceWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_provider_service(db, service_id, payload, current_user)


@vendor_router.post("/{service_id}/status", response_model=ServiceDetailRead)
def post_service_status(
    service_id: str,
    new_status: Annotated[str, Query(alias="status")],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return set_service_status(db, service_id, new_status, current_user)


@vendor_router.get(
    "/{service_id}/availability", response_model=list[AvailabilityRuleRead]
)
def get_availability(
    service_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """The service's current weekly pattern, so an editor can load before it replaces."""
    return list_availability_rules(db, service_id, current_user)


@vendor_router.put(
    "/{service_id}/availability", response_model=list[AvailabilityRuleRead]
)
def put_availability(
    service_id: str,
    rules: list[AvailabilityRuleWrite],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return replace_availability_rules(db, service_id, rules, current_user)


@vendor_router.post("/availability/exceptions", status_code=status.HTTP_201_CREATED)
def post_availability_exception(
    payload: AvailabilityExceptionWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    row = add_availability_exception(db, payload, current_user)
    return {"id": str(row.id), "exception_date": row.exception_date}


@vendor_router.get("/bookings/all", response_model=list[BookingRead])
def get_provider_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list_provider_bookings(db, current_user, status_filter=status_filter, limit=limit)


# --- Quotations -----------------------------------------------------------
#
# The quote-first flow for work that cannot be priced from a listing. Split
# across the three routers by who acts: the customer asks and accepts, the
# provider answers.

@router.post(
    "/{service_id}/quotations",
    response_model=QuotationRead,
    status_code=status.HTTP_201_CREATED,
)
def post_quotation_request(
    service_id: str,
    payload: QuotationRequestWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Describe a job and ask the provider what it would cost."""
    return create_quotation_request(db, service_id, payload, current_user)


@quotations_router.get("", response_model=list[QuotationRead])
def get_my_quotations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_my_quotations(db, current_user)


@quotations_router.post("/{quotation_id}/accept", response_model=BookingRead)
def post_accept_quotation(
    quotation_id: uuid.UUID,
    payload: QuotationAcceptWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Accept the quoted price. Raises a booking awaiting payment."""
    return accept_customer_quotation(db, quotation_id, payload, current_user)


@quotations_router.post("/{quotation_id}/decline", response_model=QuotationRead)
def post_decline_quotation(
    quotation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return decline_customer_quotation(db, quotation_id, current_user)


@vendor_router.get("/quotations/all", response_model=list[QuotationRead])
def get_provider_quotations(
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    return list_provider_quotations(db, current_user, status_filter=status_filter)


@vendor_router.post("/quotations/{quotation_id}/send", response_model=QuotationRead)
def post_send_quotation(
    quotation_id: uuid.UUID,
    payload: QuotationSendWrite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Answer a request with a price."""
    return send_provider_quotation(db, quotation_id, payload, current_user)
