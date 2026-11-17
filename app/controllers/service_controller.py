"""HTTP-facing logic for the services marketplace.

Authorization lives here, stated once per operation rather than per route:

  * The public may read only `active` listings belonging to a store that has
    services switched on.
  * A provider may touch only their own store's services and bookings.
  * A customer may see only their own bookings.
  * Status changes go through the state machine, which decides whether the
    caller's role may make that particular move.

Pricing, availability and lifecycle rules are not re-implemented here; this
module translates between HTTP and the engines that own them.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import date, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import User
from app.models.catalog import Store
from app.models.service import (
    Booking,
    ServiceQuotation,
    Service,
    ServiceAvailabilityException,
    ServiceAvailabilityRule,
    ServiceCategory,
    ServiceReview,
)
from app.services.service_availability import AvailabilityError, available_slots
from app.services.service_booking_service import (
    BookingError,
    BookingRequest,
    SlotTakenError,
    create_booking_safely,
    record_status_change,
)
from app.services.service_booking_state import BookingTransitionError
from app.services.service_notifications import (
    notify_booking_accepted,
    notify_booking_cancelled,
    notify_booking_declined,
    notify_booking_requested,
    notify_funds_released,
    notify_work_completed,
)
from app.services.service_escrow import (
    EscrowError,
    confirm_and_release,
    hold_summary,
    start_hold,
)
from app.services.service_pricing import AddonSelection, PricingError, quote_booking
from app.services.service_quotation_service import (
    QuotationError,
    accept_quotation,
    decline_quotation,
    request_quotation,
    send_quotation,
)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _not_found(detail: str = "Not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _forbidden(detail: str = "You do not have access to that.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:120] or "service"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def list_categories(db: Session, *, include_inactive: bool = False) -> list[ServiceCategory]:
    stmt = select(ServiceCategory).order_by(
        ServiceCategory.sort_order, ServiceCategory.title
    )
    if not include_inactive:
        stmt = stmt.where(ServiceCategory.is_active.is_(True))
    return list(db.scalars(stmt))


def _public_services_stmt():
    """Only live listings from stores that actually offer services.

    The store check matters: a shop can switch services off while listings
    still exist, and those must leave the catalogue immediately rather than
    waiting for someone to unpublish each one.
    """

    return (
        select(Service)
        .join(Store, Store.id == Service.store_id)
        .where(
            Service.status == "active",
            Store.is_active.is_(True),
            Store.offerings.any("services"),
        )
    )


def search_services(
    db: Session,
    *,
    query: str | None = None,
    category_id: str | None = None,
    store_id: str | None = None,
    city: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    location_mode: str | None = None,
    booking_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Service]:
    """Filtering happens in SQL, not in the client."""

    stmt = _public_services_stmt().options(selectinload(Service.images))

    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Service.title.ilike(pattern),
                Service.summary.ilike(pattern),
                Service.description.ilike(pattern),
                Store.title.ilike(pattern),
            )
        )
    if category_id:
        stmt = stmt.where(
            or_(Service.category_id == category_id, Service.subcategory_id == category_id)
        )
    if store_id:
        # One business's public services, for its storefront page — a shop that sells goods and
        # offers work should present both from the same profile.
        stmt = stmt.where(Service.store_id == store_id)
    if city:
        # Either the provider is based there or they travel there.
        stmt = stmt.where(
            or_(Store.city.ilike(city), Service.service_areas.any(city))
        )
    if min_price is not None:
        stmt = stmt.where(Service.base_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Service.base_price <= max_price)
    if min_rating is not None:
        stmt = stmt.where(Service.rating >= min_rating)
    if location_mode:
        stmt = stmt.where(Service.location_modes.any(location_mode))
    if booking_type:
        stmt = stmt.where(Service.booking_type == booking_type)

    stmt = stmt.order_by(
        Service.rating.desc().nulls_last(), Service.booking_count.desc()
    ).limit(min(limit, 100)).offset(max(offset, 0))

    return list(db.scalars(stmt))


def get_public_service(db: Session, service_id: str) -> Service:
    service = db.scalar(
        _public_services_stmt()
        .where(or_(Service.id == service_id, Service.slug == service_id))
        .options(
            selectinload(Service.packages),
            selectinload(Service.addons),
            selectinload(Service.images),
        )
    )
    if service is None:
        raise _not_found("That service is not available.")
    return service


def get_service_slots(
    db: Session,
    service_id: str,
    *,
    day: date,
    package_id: str | None = None,
    addon_ids: list[str] | None = None,
    hours: float | None = None,
):
    """Bookable times for a specific configuration.

    Takes the configuration rather than just the service because duration
    depends on it — offering slots sized for the base service would hand the
    customer a time that cannot fit their Premium package plus two extras.
    """

    service = get_public_service(db, service_id)
    try:
        quote = quote_booking(
            service,
            package_id=package_id,
            addons=[AddonSelection(addon_id=a, quantity=1) for a in (addon_ids or [])],
            hours=hours,
        )
    except PricingError as exc:
        raise _bad_request(str(exc)) from exc

    if quote.duration_minutes <= 0:
        return []

    try:
        return available_slots(
            db, service, day=day, duration_minutes=quote.duration_minutes
        )
    except AvailabilityError as exc:
        raise _bad_request(str(exc)) from exc


def quote_service(db: Session, service_id: str, payload) -> object:
    service = get_public_service(db, service_id)
    try:
        return quote_booking(
            service,
            package_id=str(payload.package_id) if payload.package_id else None,
            addons=[
                AddonSelection(addon_id=str(item.addon_id), quantity=item.quantity)
                for item in payload.addons
            ],
            hours=payload.hours,
        )
    except PricingError as exc:
        raise _bad_request(str(exc)) from exc


# --------------------------------------------------------------------------
# Customer bookings
# --------------------------------------------------------------------------

def create_customer_booking(
    db: Session, service_id: str, payload, current_user: User
) -> Booking:
    service = get_public_service(db, service_id)

    request = BookingRequest(
        service_id=service.id,
        customer_user_id=current_user.id,
        package_id=str(payload.package_id) if payload.package_id else None,
        addons=tuple(
            AddonSelection(addon_id=str(item.addon_id), quantity=item.quantity)
            for item in payload.addons
        ),
        hours=payload.hours,
        start_at=payload.start_at,
        location_mode=payload.location_mode,
        location_address=payload.location_address,
        location_city=payload.location_city,
        customer_note=payload.customer_note,
    )

    try:
        booking = create_booking_safely(db, service, request)
    except SlotTakenError as exc:
        # 409: the request was valid, the world moved. A client can retry
        # with a different slot, which a 400 would not suggest.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BookingError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(booking)

    # After the commit: the booking exists whether or not the push lands, and
    # a notification outage must never undo someone's booking.
    if booking.status == "pending_provider":
        notify_booking_requested(db, booking)
        db.commit()

    return booking


def list_my_bookings(
    db: Session, current_user: User, *, status_filter: str | None = None, limit: int = 30
) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.customer_user_id == current_user.id)
        .options(selectinload(Booking.addons))
        .order_by(Booking.created_at.desc())
        .limit(min(limit, 100))
    )
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    return list(db.scalars(stmt))


def _load_booking_for(db: Session, booking_id: uuid.UUID, user: User) -> tuple[Booking, str]:
    """Fetch a booking and work out what the caller is to it."""

    booking = db.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(selectinload(Booking.addons), selectinload(Booking.status_history))
    )
    if booking is None:
        raise _not_found("Booking not found.")

    from app.models.user import UserRole

    if user.role == UserRole.ADMIN:
        return booking, "admin"
    if booking.customer_user_id == user.id:
        return booking, "customer"
    if booking.provider_user_id and booking.provider_user_id == user.id:
        return booking, "provider"
    # Deliberately 404, not 403: confirming a booking exists to someone
    # unrelated to it leaks that it exists at all.
    raise _not_found("Booking not found.")


def get_booking(db: Session, booking_id: uuid.UUID, current_user: User) -> tuple[Booking, str]:
    return _load_booking_for(db, booking_id, current_user)


def transition_booking(
    db: Session, booking_id: uuid.UUID, payload, current_user: User
) -> Booking:
    booking, actor_role = _load_booking_for(db, booking_id, current_user)

    try:
        record_status_change(
            db,
            booking,
            to_status=payload.to_status,
            actor_role=actor_role,
            actor_user_id=current_user.id,
            note=payload.note,
        )
    except BookingTransitionError as exc:
        raise _bad_request(str(exc)) from exc

    # Completion starts the hold; it does not pay. The provider saying the
    # work is done is a claim, and releasing on that claim alone would mean
    # the escrow protected nobody. The customer confirming, or the grace
    # window elapsing, is what actually pays -- see `confirm_booking_work`.
    if booking.status == "completed" and booking.work_completed_at is None:
        start_hold(booking)

    db.commit()
    db.refresh(booking)

    # Announced after the commit, for the same reason as above: the state
    # change is the product, the notification is the courtesy.
    _announce_transition(db, booking, actor_role=actor_role)
    db.commit()

    return booking


def _announce_transition(db: Session, booking: Booking, *, actor_role: str) -> None:
    """Tell the party who did not cause the change.

    Whoever acted already knows; notifying them back is noise, and on a
    cancellation it reads as the system blaming them for their own decision.
    """

    status_value = booking.status
    if status_value == "pending_payment":
        notify_booking_accepted(db, booking)
    elif status_value == "declined":
        notify_booking_declined(db, booking)
    elif status_value == "completed":
        notify_work_completed(db, booking)
    elif status_value == "cancelled":
        notify_booking_cancelled(db, booking, by_role=actor_role)


# --------------------------------------------------------------------------
# Provider management
# --------------------------------------------------------------------------

def _provider_store(db: Session, user: User, store_id: str | None = None) -> Store:
    """The store this provider is acting as, with services enabled."""

    stmt = select(Store).where(Store.vendor_user_id == user.id)
    if store_id:
        stmt = stmt.where(Store.id == store_id)
    store = db.scalar(stmt)
    if store is None:
        raise _forbidden("You do not manage a store.")
    if not (store.offerings or []) or "services" not in (store.offerings or []):
        raise _forbidden(
            "Turn on services for your store before publishing a service."
        )
    return store


def _owned_service(db: Session, service_id: str, user: User) -> Service:
    service = db.scalar(
        select(Service)
        .where(Service.id == service_id)
        .options(selectinload(Service.packages), selectinload(Service.addons))
    )
    if service is None:
        raise _not_found("Service not found.")
    store = db.get(Store, service.store_id)
    if store is None or store.vendor_user_id != user.id:
        raise _not_found("Service not found.")
    return service


def list_provider_services(db: Session, current_user: User) -> list[Service]:
    store = _provider_store(db, current_user)
    return list(
        db.scalars(
            select(Service)
            .where(Service.store_id == store.id)
            .options(selectinload(Service.images))
            .order_by(Service.created_at.desc())
        )
    )


def create_provider_service(db: Session, payload, current_user: User) -> Service:
    store = _provider_store(db, current_user)

    base_slug = _slugify(payload.title)
    slug = base_slug
    while db.scalar(select(Service.id).where(Service.slug == slug)):
        slug = f"{base_slug}-{secrets.randbelow(9000) + 1000}"

    service = Service(
        id=f"svc_{uuid.uuid4().hex[:20]}",
        slug=slug,
        store_id=store.id,
        vendor_user_id=current_user.id,
        # New listings are drafts. Publication is a separate, reviewable act
        # so nothing reaches the public catalogue just by being created.
        status="draft",
        **payload.model_dump(exclude_unset=False),
    )
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def update_provider_service(
    db: Session, service_id: str, payload, current_user: User
) -> Service:
    service = _owned_service(db, service_id, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)
    db.commit()
    db.refresh(service)
    return service


def set_service_status(
    db: Session, service_id: str, new_status: str, current_user: User
) -> Service:
    """Providers may publish, pause and archive; they may not self-approve."""

    allowed = {"draft", "pending_review", "paused", "archived"}
    if new_status not in allowed:
        raise _bad_request("You cannot set that status.")

    service = _owned_service(db, service_id, current_user)

    # Paused listings go straight back to active: they were approved once and
    # pausing is a provider's own business, not a re-review trigger.
    if new_status == "pending_review" and service.status == "paused":
        new_status = "active"

    service.status = new_status
    db.commit()
    db.refresh(service)
    return service


def list_provider_bookings(
    db: Session, current_user: User, *, status_filter: str | None = None, limit: int = 50
) -> list[Booking]:
    store = _provider_store(db, current_user)
    stmt = (
        select(Booking)
        .where(Booking.store_id == store.id)
        .options(selectinload(Booking.addons))
        .order_by(Booking.scheduled_start_at.asc().nulls_last(), Booking.created_at.desc())
        .limit(min(limit, 200))
    )
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    return list(db.scalars(stmt))


def list_availability_rules(
    db: Session, service_id: str, current_user: User
) -> list[ServiceAvailabilityRule]:
    """The current weekly pattern for one service.

    `replace_availability_rules` is a whole-week write on the premise that the client holds the
    complete picture — but nothing returned that picture, so a provider could only replace their
    hours blind. An editor opening on an empty week would wipe real availability on save.
    """

    service = _owned_service(db, service_id, current_user)
    return list(
        db.scalars(
            select(ServiceAvailabilityRule)
            .where(ServiceAvailabilityRule.service_id == service.id)
            .order_by(
                ServiceAvailabilityRule.weekday,
                ServiceAvailabilityRule.start_time,
            )
        )
    )


def replace_availability_rules(
    db: Session, service_id: str, rules, current_user: User
) -> list[ServiceAvailabilityRule]:
    """Replace the weekly pattern wholesale.

    A whole-week replace rather than per-row edits: the client always holds
    the complete picture, and diffing rows invites a half-applied week where
    a provider is open on a day they closed.
    """

    service = _owned_service(db, service_id, current_user)

    db.query(ServiceAvailabilityRule).filter(
        ServiceAvailabilityRule.service_id == service.id
    ).delete(synchronize_session=False)

    created = []
    for rule in rules:
        row = ServiceAvailabilityRule(
            service_id=service.id,
            weekday=rule.weekday,
            start_time=rule.start_time,
            end_time=rule.end_time,
        )
        db.add(row)
        created.append(row)

    db.commit()
    return created


def add_availability_exception(
    db: Session, payload, current_user: User
) -> ServiceAvailabilityException:
    store = _provider_store(db, current_user)
    if payload.service_id:
        _owned_service(db, payload.service_id, current_user)

    existing = db.scalar(
        select(ServiceAvailabilityException).where(
            ServiceAvailabilityException.store_id == store.id,
            ServiceAvailabilityException.service_id == payload.service_id,
            ServiceAvailabilityException.exception_date == payload.exception_date,
        )
    )
    row = existing or ServiceAvailabilityException(
        store_id=store.id,
        service_id=payload.service_id,
        exception_date=payload.exception_date,
    )
    row.is_available = payload.is_available
    row.start_time = payload.start_time
    row.end_time = payload.end_time
    row.note = payload.note
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------

def create_service_review(
    db: Session, booking_id: uuid.UUID, payload, current_user: User
) -> ServiceReview:
    """Only the customer, only once, only after the work is done."""

    booking = db.get(Booking, booking_id)
    if booking is None or booking.customer_user_id != current_user.id:
        raise _not_found("Booking not found.")
    if booking.status != "completed":
        raise _bad_request("You can review a service once it has been completed.")

    if db.scalar(select(ServiceReview.id).where(ServiceReview.booking_id == booking.id)):
        raise _bad_request("You have already reviewed this booking.")

    review = ServiceReview(
        booking_id=booking.id,
        service_id=booking.service_id,
        store_id=booking.store_id,
        customer_user_id=current_user.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(review)
    db.flush()

    # Recompute from the table rather than nudging a running average, which
    # drifts as reviews are hidden by moderation.
    aggregate = db.execute(
        select(func.avg(ServiceReview.rating), func.count(ServiceReview.id)).where(
            ServiceReview.service_id == booking.service_id,
            ServiceReview.status == "published",
        )
    ).one()
    service = db.get(Service, booking.service_id)
    if service is not None:
        service.rating = float(aggregate[0]) if aggregate[0] is not None else None
        service.review_count = int(aggregate[1] or 0)

    db.commit()
    db.refresh(review)
    return review


def confirm_booking_work(
    db: Session, booking_id: uuid.UUID, current_user: User
) -> Booking:
    """Customer confirms the work, releasing the held funds to the provider.

    Only the customer may do this. An admin releasing a disputed booking goes
    through the admin path so the reason is recorded as such, and a provider
    can never confirm their own work -- that would make the hold decorative.
    """

    booking, actor_role = _load_booking_for(db, booking_id, current_user)

    if actor_role != "customer":
        raise _forbidden("Only the customer can confirm the work.")

    try:
        released = confirm_and_release(db, booking)
    except EscrowError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(booking)

    if released > 0:
        notify_funds_released(db, booking, amount=released)
        db.commit()

    return booking


def get_booking_hold(db: Session, booking_id: uuid.UUID, current_user: User) -> dict:
    """What is being held on this booking, and when it releases."""

    booking, _ = _load_booking_for(db, booking_id, current_user)
    return hold_summary(booking)


# --------------------------------------------------------------------------
# Quotations
# --------------------------------------------------------------------------

def _load_quotation_for(
    db: Session, quotation_id: uuid.UUID, user: User
) -> tuple[ServiceQuotation, str]:
    quotation = db.get(ServiceQuotation, quotation_id)
    if quotation is None:
        raise _not_found("Quotation not found.")

    from app.models.user import UserRole

    if user.role == UserRole.ADMIN:
        return quotation, "admin"
    if quotation.customer_user_id == user.id:
        return quotation, "customer"

    store = db.get(Store, quotation.store_id)
    if store is not None and store.vendor_user_id == user.id:
        return quotation, "provider"

    # 404 rather than 403: confirming it exists to an unrelated party leaks
    # that this customer approached this provider.
    raise _not_found("Quotation not found.")


def create_quotation_request(
    db: Session, service_id: str, payload, current_user: User
) -> ServiceQuotation:
    service = get_public_service(db, service_id)
    try:
        quotation = request_quotation(
            db,
            service,
            customer_user_id=current_user.id,
            brief=payload.brief,
            preferred_date=payload.preferred_date,
            location_mode=payload.location_mode,
            location_city=payload.location_city,
        )
    except QuotationError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(quotation)
    return quotation


def list_my_quotations(db: Session, current_user: User) -> list[ServiceQuotation]:
    return list(
        db.scalars(
            select(ServiceQuotation)
            .where(ServiceQuotation.customer_user_id == current_user.id)
            .order_by(ServiceQuotation.created_at.desc())
            .limit(100)
        )
    )


def list_provider_quotations(
    db: Session, current_user: User, *, status_filter: str | None = None
) -> list[ServiceQuotation]:
    store = _provider_store(db, current_user)
    stmt = (
        select(ServiceQuotation)
        .where(ServiceQuotation.store_id == store.id)
        .order_by(ServiceQuotation.created_at.desc())
        .limit(200)
    )
    if status_filter:
        stmt = stmt.where(ServiceQuotation.status == status_filter)
    return list(db.scalars(stmt))


def send_provider_quotation(
    db: Session, quotation_id: uuid.UUID, payload, current_user: User
) -> ServiceQuotation:
    quotation, actor_role = _load_quotation_for(db, quotation_id, current_user)
    if actor_role not in ("provider", "admin"):
        raise _forbidden("Only the provider can quote on this request.")

    try:
        send_quotation(
            db,
            quotation,
            amount=payload.amount,
            message=payload.message,
            estimated_duration_minutes=payload.estimated_duration_minutes,
            validity_days=payload.validity_days,
        )
    except QuotationError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(quotation)
    return quotation


def accept_customer_quotation(
    db: Session, quotation_id: uuid.UUID, payload, current_user: User
) -> Booking:
    """Accepting raises a booking at the quoted price, awaiting payment."""

    quotation, actor_role = _load_quotation_for(db, quotation_id, current_user)
    if actor_role != "customer":
        raise _forbidden("Only the customer can accept a quotation.")

    try:
        booking = accept_quotation(
            db,
            quotation,
            start_at=payload.start_at,
            location_address=payload.location_address,
            customer_note=payload.customer_note,
        )
    except QuotationError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(booking)
    return booking


def decline_customer_quotation(
    db: Session, quotation_id: uuid.UUID, current_user: User
) -> ServiceQuotation:
    quotation, actor_role = _load_quotation_for(db, quotation_id, current_user)
    if actor_role not in ("customer", "provider"):
        raise _forbidden("You cannot close this quotation.")

    try:
        decline_quotation(quotation, by_provider=actor_role == "provider")
    except QuotationError as exc:
        raise _bad_request(str(exc)) from exc

    db.commit()
    db.refresh(quotation)
    return quotation
