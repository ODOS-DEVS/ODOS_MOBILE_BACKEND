"""Admin oversight of price negotiations.

Read-only by default. The one write — switching negotiation off for a product or
a whole vendor — is a moderation action, so it is audited and it deliberately
does not touch agreements that were already struck: cancelling a bargain
somebody is relying on is a separate decision from stopping new ones.

Nothing here exposes payment detail or personal data beyond what the admin's
existing permissions already allow; it adds the negotiation record, which is
what a dispute actually turns on.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Product, Store, User
from app.models.negotiation import (
    AgreementStatus,
    NegotiationAgreement,
    NegotiationOffer,
    PriceNegotiation,
)
from app.schemas.negotiation import (
    AdminNegotiationDetailRead,
    AdminNegotiationRead,
    AdminNegotiationStatsRead,
)
from app.schemas.pagination import AdminPageRead
from app.services.event_log_service import record_event

logger = logging.getLogger(__name__)


def _summarize(db: Session, negotiation: PriceNegotiation) -> AdminNegotiationRead:
    product = db.get(Product, negotiation.product_id)
    store = db.get(Store, negotiation.store_id)
    customer = db.get(User, negotiation.customer_user_id)
    vendor = db.get(User, negotiation.vendor_user_id)
    agreement = db.scalar(
        select(NegotiationAgreement)
        .where(NegotiationAgreement.negotiation_id == negotiation.id)
        .order_by(NegotiationAgreement.created_at.desc())
    )
    latest = db.scalar(
        select(NegotiationOffer)
        .where(NegotiationOffer.negotiation_id == negotiation.id)
        .order_by(NegotiationOffer.created_at.desc())
    )

    return AdminNegotiationRead(
        id=negotiation.id,
        reference=negotiation.reference,
        status=negotiation.status,
        product_id=negotiation.product_id,
        product_title=product.title if product else negotiation.product_id,
        product_image_url=product.image_url if product else None,
        store_id=negotiation.store_id,
        store_name=store.title if store else None,
        vendor_user_id=negotiation.vendor_user_id,
        vendor_name=(vendor.full_name or vendor.email) if vendor else None,
        customer_user_id=negotiation.customer_user_id,
        customer_name=(customer.full_name or customer.email) if customer else None,
        listed_unit_price=negotiation.listed_unit_price,
        latest_offer_price=latest.unit_price if latest else None,
        agreed_unit_price=agreement.agreed_unit_price if agreement else None,
        quantity=agreement.quantity if agreement else (latest.quantity if latest else None),
        agreement_reference=agreement.reference if agreement else None,
        agreement_status=agreement.status if agreement else None,
        order_number=agreement.order_number if agreement else None,
        created_at=negotiation.created_at,
        updated_at=negotiation.updated_at,
    )


def list_admin_negotiations(
    db: Session,
    *,
    search: str | None = None,
    status_filter: str | None = None,
    vendor_user_id: uuid.UUID | None = None,
    customer_user_id: uuid.UUID | None = None,
    product_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AdminPageRead[AdminNegotiationRead]:
    """Search negotiations by reference, agreement reference, product or either party."""
    query = select(PriceNegotiation).order_by(PriceNegotiation.updated_at.desc())

    cleaned = (status_filter or "").strip().lower()
    if cleaned and cleaned != "all":
        query = query.where(PriceNegotiation.status == cleaned)
    if vendor_user_id:
        query = query.where(PriceNegotiation.vendor_user_id == vendor_user_id)
    if customer_user_id:
        query = query.where(PriceNegotiation.customer_user_id == customer_user_id)
    if product_id:
        query = query.where(PriceNegotiation.product_id == product_id)

    term = (search or "").strip()
    if term:
        # A dispute arrives as one of: the negotiation reference, the agreement
        # reference, an order number, or a product name somebody typed from memory.
        agreement_matches = select(NegotiationAgreement.negotiation_id).where(
            or_(
                NegotiationAgreement.reference.ilike(f"%{term}%"),
                NegotiationAgreement.order_number.ilike(f"%{term}%"),
            )
        )
        product_matches = select(Product.id).where(Product.title.ilike(f"%{term}%"))
        query = query.where(
            or_(
                PriceNegotiation.reference.ilike(f"%{term}%"),
                PriceNegotiation.id.in_(agreement_matches),
                PriceNegotiation.product_id.in_(product_matches),
            )
        )

    rows = list(db.scalars(query.offset(offset).limit(limit + 1)).all())
    has_more = len(rows) > limit
    return AdminPageRead(
        items=[_summarize(db, row) for row in rows[:limit]],
        has_more=has_more,
    )


def get_admin_negotiation(db: Session, negotiation_id: uuid.UUID) -> AdminNegotiationDetailRead:
    """One negotiation with every offer, the agreement, and the order it became.

    This is the dispute view: the full sequence, with timestamps, exactly as it
    happened.
    """
    from app.controllers.negotiation_controller import _serialize_agreement
    from app.schemas.negotiation import NegotiationOfferRead

    negotiation = db.scalar(
        select(PriceNegotiation)
        .options(selectinload(PriceNegotiation.offers))
        .where(PriceNegotiation.id == negotiation_id)
    )
    if not negotiation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negotiation not found.")

    agreement = db.scalar(
        select(NegotiationAgreement)
        .where(NegotiationAgreement.negotiation_id == negotiation.id)
        .order_by(NegotiationAgreement.created_at.desc())
    )
    product = db.get(Product, negotiation.product_id)

    return AdminNegotiationDetailRead(
        **_summarize(db, negotiation).model_dump(),
        current_listed_price=product.price if product else None,
        product_is_negotiable=bool(product.is_negotiable) if product else False,
        selected_color=negotiation.selected_color,
        selected_size=negotiation.selected_size,
        offers=[NegotiationOfferRead.model_validate(o) for o in negotiation.offers],
        agreement=_serialize_agreement(agreement),
    )


def negotiation_stats(db: Session) -> AdminNegotiationStatsRead:
    """Counters for the overview strip, and the things worth looking at twice."""
    by_status = {
        str(name): int(count)
        for name, count in db.execute(
            select(PriceNegotiation.status, func.count(PriceNegotiation.id)).group_by(
                PriceNegotiation.status
            )
        ).all()
    }
    agreements_by_status = {
        str(name): int(count)
        for name, count in db.execute(
            select(NegotiationAgreement.status, func.count(NegotiationAgreement.id)).group_by(
                NegotiationAgreement.status
            )
        ).all()
    }
    negotiable_products = int(
        db.scalar(select(func.count(Product.id)).where(Product.is_negotiable.is_(True))) or 0
    )
    # The deepest discount anyone actually bought at — the single most useful
    # number for spotting a vendor being gamed, or gaming.
    deepest = db.execute(
        select(
            NegotiationAgreement.reference,
            NegotiationAgreement.listed_unit_price,
            NegotiationAgreement.agreed_unit_price,
        )
        .where(NegotiationAgreement.status == AgreementStatus.USED.value)
        .order_by(
            (NegotiationAgreement.agreed_unit_price * 100 / NegotiationAgreement.listed_unit_price).asc()
        )
        .limit(1)
    ).first()

    return AdminNegotiationStatsRead(
        total=sum(by_status.values()),
        open=by_status.get("open", 0),
        agreed=by_status.get("agreed", 0),
        completed=by_status.get("completed", 0),
        rejected=by_status.get("rejected", 0),
        expired=by_status.get("expired", 0),
        agreements_live=agreements_by_status.get(AgreementStatus.ACCEPTED.value, 0),
        agreements_used=agreements_by_status.get(AgreementStatus.USED.value, 0),
        agreements_expired=agreements_by_status.get(AgreementStatus.EXPIRED.value, 0),
        negotiable_products=negotiable_products,
        deepest_discount_reference=deepest[0] if deepest else None,
        deepest_discount_percent=(
            int(round((1 - deepest[2] / deepest[1]) * 100)) if deepest and deepest[1] else None
        ),
    )


def set_product_negotiable(
    db: Session,
    admin: User,
    product_id: str,
    *,
    enabled: bool,
    reason: str | None = None,
) -> dict:
    """Switch negotiation off (or back on) for one product, with an audit entry.

    Agreements already struck are left alone on purpose. Stopping new haggling is
    a moderation call; voiding a price a shopper was promised is a refund
    decision, and conflating the two would let one click quietly break deals.
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    before = bool(product.is_negotiable)
    product.is_negotiable = enabled
    if not enabled:
        product.min_acceptable_price = None
    db.flush()

    record_event(
        db,
        event_type="admin.negotiation",
        actor_type="admin",
        actor_id=str(admin.id),
        action="product_negotiation_enabled" if enabled else "product_negotiation_disabled",
        entity_type="product",
        entity_id=product_id,
        metadata={"reason": reason} if reason else None,
        before_state={"is_negotiable": before},
        after_state={"is_negotiable": enabled},
        commit=False,
    )
    db.commit()
    return {"product_id": product_id, "is_negotiable": enabled}


def set_vendor_negotiable(
    db: Session,
    admin: User,
    vendor_user_id: uuid.UUID,
    *,
    enabled: bool,
    reason: str | None = None,
) -> dict:
    """Switch negotiation off across every product a vendor owns.

    The blunt instrument, for a vendor found abusing the feature. Same rule as
    the per-product version: live agreements survive.
    """
    products = list(
        db.scalars(select(Product).where(Product.vendor_user_id == vendor_user_id)).all()
    )
    changed = 0
    for product in products:
        if bool(product.is_negotiable) == enabled:
            continue
        product.is_negotiable = enabled
        if not enabled:
            product.min_acceptable_price = None
        changed += 1
    db.flush()

    record_event(
        db,
        event_type="admin.negotiation",
        actor_type="admin",
        actor_id=str(admin.id),
        action="vendor_negotiation_enabled" if enabled else "vendor_negotiation_disabled",
        entity_type="user",
        entity_id=str(vendor_user_id),
        metadata={"reason": reason, "products_changed": changed},
        before_state=None,
        after_state={"is_negotiable": enabled},
        commit=False,
    )
    db.commit()
    logger.info(
        "Admin %s set negotiation=%s for vendor %s (%s products)",
        admin.id,
        enabled,
        vendor_user_id,
        changed,
    )
    return {"vendor_user_id": str(vendor_user_id), "is_negotiable": enabled, "products_changed": changed}


def cancel_agreement(
    db: Session,
    admin: User,
    agreement_id: uuid.UUID,
    *,
    reason: str | None = None,
) -> dict:
    """Void a live agreement — a dispute outcome, not routine moderation.

    Refuses an agreement already spent on an order: that is a refund, handled by
    the order tools, and pretending otherwise here would leave an order priced
    against a cancelled agreement.
    """
    agreement = db.get(NegotiationAgreement, agreement_id)
    if not agreement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agreement not found.")
    if agreement.status == AgreementStatus.USED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That agreement was already used for an order. Refund the order instead.",
        )
    if agreement.status != AgreementStatus.ACCEPTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That agreement is already {agreement.status}.",
        )

    agreement.status = AgreementStatus.CANCELLED.value
    agreement.used_at = datetime.now(UTC)
    negotiation = db.get(PriceNegotiation, agreement.negotiation_id)
    if negotiation:
        negotiation.status = "rejected"
    db.flush()

    record_event(
        db,
        event_type="admin.negotiation",
        actor_type="admin",
        actor_id=str(admin.id),
        action="agreement_cancelled",
        entity_type="negotiation_agreement",
        entity_id=str(agreement_id),
        metadata={"reason": reason, "reference": agreement.reference},
        before_state={"status": AgreementStatus.ACCEPTED.value},
        after_state={"status": AgreementStatus.CANCELLED.value},
        commit=False,
    )
    db.commit()
    return {"agreement_id": str(agreement_id), "status": agreement.status}
