"""Administering the services marketplace.

Covers what operators need that neither side of a booking can do for itself:
settling disputes, approving listings into the public catalogue, verifying
providers, and maintaining the category tree every client reads.

Authorization is by feature band, like the rest of the admin surface. The one
rule stated here rather than inherited: resolving a dispute moves money, so it
goes through the same escrow release the customer's confirmation uses — an
admin cannot pay a provider by editing a status field.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import User
from app.models.catalog import Store
from app.models.service import (
    Booking,
    Service,
    ServiceCategory,
    ServiceQuotation,
    ServiceReview,
)
from app.services.service_booking_service import record_status_change
from app.services.service_booking_state import BookingTransitionError
from app.services.service_escrow import RELEASE_ADMIN, confirm_and_release
from app.services.service_notifications import (
    notify_booking_cancelled,
    notify_funds_released,
)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80]


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

def list_all_categories(db: Session) -> list[ServiceCategory]:
    """Including inactive ones — admins maintain the tree, not browse it."""

    return list(
        db.scalars(
            select(ServiceCategory).order_by(
                ServiceCategory.sort_order, ServiceCategory.title
            )
        )
    )


def create_category(db: Session, payload) -> ServiceCategory:
    slug = _slugify(payload.slug or payload.title)
    if db.scalar(select(ServiceCategory.id).where(ServiceCategory.slug == slug)):
        raise _bad_request("A category with that slug already exists.")
    if payload.parent_id and not db.get(ServiceCategory, payload.parent_id):
        raise _bad_request("That parent category does not exist.")

    category = ServiceCategory(
        id=f"svccat_{uuid.uuid4().hex[:16]}",
        **{**payload.model_dump(), "slug": slug},
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category_id: str, payload) -> ServiceCategory:
    category = db.get(ServiceCategory, category_id)
    if category is None:
        raise _not_found("Category not found.")

    data = payload.model_dump(exclude_unset=True)
    if "parent_id" in data and data["parent_id"] == category_id:
        # A category that parents itself disappears from every tree walk.
        raise _bad_request("A category cannot be its own parent.")
    if "slug" in data and data["slug"]:
        data["slug"] = _slugify(data["slug"])
        clash = db.scalar(
            select(ServiceCategory.id).where(
                ServiceCategory.slug == data["slug"], ServiceCategory.id != category_id
            )
        )
        if clash:
            raise _bad_request("A category with that slug already exists.")

    for field, value in data.items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category


def deactivate_category(db: Session, category_id: str) -> ServiceCategory:
    """Hide a category rather than delete it.

    Deleting would orphan every listing filed under it and break the links
    customers have already shared. Deactivating removes it from discovery
    while the listings stay reachable and re-filable.
    """

    category = db.get(ServiceCategory, category_id)
    if category is None:
        raise _not_found("Category not found.")
    category.is_active = False
    db.commit()
    db.refresh(category)
    return category


# --------------------------------------------------------------------------
# Listing moderation
# --------------------------------------------------------------------------

def list_services_for_admin(
    db: Session,
    *,
    status_filter: str | None = None,
    store_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Service]:
    stmt = (
        select(Service)
        .options(selectinload(Service.images))
        .order_by(Service.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
    )
    if status_filter:
        stmt = stmt.where(Service.status == status_filter)
    if store_id:
        stmt = stmt.where(Service.store_id == store_id)
    return list(db.scalars(stmt))


def set_service_admin_status(
    db: Session, service_id: str, new_status: str, *, reason: str | None = None
) -> Service:
    """Approve, reject or suspend a listing.

    Only an admin can move a listing to `active`; a provider submitting for
    review cannot approve themselves. Existing bookings are deliberately
    untouched — suspending a listing stops new bookings, it does not cancel
    work people have already paid for.
    """

    allowed = {"active", "rejected", "paused", "archived", "pending_review"}
    if new_status not in allowed:
        raise _bad_request(f"Status must be one of {', '.join(sorted(allowed))}.")

    service = db.get(Service, service_id)
    if service is None:
        raise _not_found("Service not found.")

    if new_status == "rejected" and not (reason or "").strip():
        # A rejection the provider cannot act on just generates a support
        # ticket, so the reason is required rather than optional.
        raise _bad_request("Give a reason so the provider can fix the listing.")

    service.status = new_status
    service.rejection_reason = reason if new_status == "rejected" else None
    db.commit()
    db.refresh(service)
    return service


def set_provider_verification(
    db: Session, store_id: str, verification: str
) -> Store:
    allowed = {"unverified", "pending", "verified"}
    if verification not in allowed:
        raise _bad_request(f"Verification must be one of {', '.join(sorted(allowed))}.")

    store = db.get(Store, store_id)
    if store is None:
        raise _not_found("Store not found.")
    store.provider_verification = verification
    db.commit()
    db.refresh(store)
    return store


def set_store_offerings(db: Session, store_id: str, offerings: list[str]) -> Store:
    """Switch products and/or services on for a store."""

    known = {"products", "services"}
    unknown = sorted(set(offerings) - known)
    if unknown:
        raise _bad_request(f"Unknown offering: {', '.join(unknown)}.")
    if not offerings:
        raise _bad_request("A store must offer something.")

    store = db.get(Store, store_id)
    if store is None:
        raise _not_found("Store not found.")
    store.offerings = offerings
    db.commit()
    db.refresh(store)
    return store


# --------------------------------------------------------------------------
# Bookings and disputes
# --------------------------------------------------------------------------

def list_bookings_for_admin(
    db: Session,
    *,
    status_filter: str | None = None,
    store_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Booking]:
    stmt = (
        select(Booking)
        .options(selectinload(Booking.addons))
        .order_by(Booking.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
    )
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    if store_id:
        stmt = stmt.where(Booking.store_id == store_id)
    return list(db.scalars(stmt))


def resolve_dispute(
    db: Session,
    booking_id: uuid.UUID,
    *,
    outcome: str,
    note: str,
    admin_user: User,
) -> Booking:
    """Settle a disputed booking, in the provider's or the customer's favour.

    Two outcomes, and they mean different things to the money:

      * `completed` — the work stands. The held funds release to the
        provider through the ordinary escrow path, so the ledger entry and
        the wallet credit are identical to a customer confirmation; only the
        recorded reason differs.
      * `cancelled` — the work does not stand. The hold is *not* released;
        the funds stay in treasury for the refund process, which is the
        existing one rather than a services-specific invention.
    """

    if outcome not in ("completed", "cancelled"):
        raise _bad_request("A dispute resolves to completed or cancelled.")
    if not (note or "").strip():
        # The note is the audit trail. A resolution nobody can explain later
        # is indistinguishable from an admin reaching into the money.
        raise _bad_request("Record why the dispute was resolved this way.")

    booking = db.scalar(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    )
    if booking is None:
        raise _not_found("Booking not found.")
    if booking.status != "disputed":
        raise _bad_request("This booking is not under dispute.")

    try:
        record_status_change(
            db,
            booking,
            to_status=outcome,
            actor_role="admin",
            actor_user_id=admin_user.id,
            note=note.strip(),
        )
    except BookingTransitionError as exc:
        raise _bad_request(str(exc)) from exc

    released = 0.0
    if outcome == "completed":
        released = confirm_and_release(db, booking, reason=RELEASE_ADMIN)

    db.commit()
    db.refresh(booking)

    if released > 0:
        notify_funds_released(db, booking, amount=released)
        db.commit()
    elif outcome == "cancelled":
        notify_booking_cancelled(db, booking, by_role="admin")
        db.commit()

    return booking


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

def services_marketplace_overview(db: Session, *, window_days: int = 30) -> dict:
    """Counts an operator actually acts on, not vanity totals."""

    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    def _count(stmt) -> int:
        return int(db.scalar(stmt) or 0)

    providers = _count(
        select(func.count(Store.id)).where(Store.offerings.any("services"))
    )
    awaiting_verification = _count(
        select(func.count(Store.id)).where(
            Store.offerings.any("services"), Store.provider_verification == "pending"
        )
    )
    pending_listings = _count(
        select(func.count(Service.id)).where(Service.status == "pending_review")
    )
    live_listings = _count(
        select(func.count(Service.id)).where(Service.status == "active")
    )
    open_disputes = _count(
        select(func.count(Booking.id)).where(Booking.status == "disputed")
    )
    awaiting_release = _count(
        select(func.count(Booking.id)).where(
            Booking.status == "completed", Booking.earnings_released_at.is_(None)
        )
    )
    held_amount = float(
        db.scalar(
            select(func.coalesce(func.sum(Booking.amount_paid), 0.0)).where(
                Booking.status == "completed",
                Booking.earnings_released_at.is_(None),
            )
        )
        or 0.0
    )
    bookings_in_window = _count(
        select(func.count(Booking.id)).where(Booking.created_at >= since)
    )
    gross_in_window = float(
        db.scalar(
            select(func.coalesce(func.sum(Booking.amount_paid), 0.0)).where(
                Booking.created_at >= since
            )
        )
        or 0.0
    )
    commission_in_window = float(
        db.scalar(
            select(func.coalesce(func.sum(Booking.commission_amount), 0.0)).where(
                Booking.created_at >= since, Booking.payment_status == "paid"
            )
        )
        or 0.0
    )
    open_quotations = _count(
        select(func.count(ServiceQuotation.id)).where(
            ServiceQuotation.status.in_(("requested", "sent"))
        )
    )
    average_rating = db.scalar(
        select(func.avg(ServiceReview.rating)).where(ServiceReview.status == "published")
    )

    return {
        "window_days": window_days,
        "providers": providers,
        "providers_awaiting_verification": awaiting_verification,
        "listings_pending_review": pending_listings,
        "listings_live": live_listings,
        "open_disputes": open_disputes,
        "bookings_awaiting_release": awaiting_release,
        # What Odos is currently holding on behalf of providers.
        "amount_held_in_escrow": round(held_amount, 2),
        "bookings_in_window": bookings_in_window,
        "gross_collected_in_window": round(gross_in_window, 2),
        "commission_in_window": round(commission_in_window, 2),
        "open_quotations": open_quotations,
        "average_service_rating": (
            round(float(average_rating), 2) if average_rating is not None else None
        ),
    }
