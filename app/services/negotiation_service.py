"""Rules for price negotiation, kept away from the controllers that call them.

Everything here answers one of two questions: is this move allowed, and what
price does it settle on. Nothing here trusts a number that arrived from a
browser — prices are read from the product row and the agreement row.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Product
from app.models.negotiation import (
    AgreementStatus,
    NegotiationAgreement,
    NegotiationStatus,
    OfferStatus,
    PriceNegotiation,
)

#: References a human can read down the phone during a dispute.
_REFERENCE_ALPHABET = "0123456789"


def _reference(prefix: str) -> str:
    return f"{prefix}-{''.join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(6))}"


def negotiation_reference() -> str:
    return _reference("NEG")


def agreement_reference() -> str:
    return _reference("AGR")


def agreement_expiry(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return current + timedelta(hours=max(1, int(settings.negotiation_agreement_hours)))


def product_accepts_negotiation(product: Product) -> bool:
    """Whether a *new* negotiation may be opened on this product.

    Deliberately not the same question as whether an existing agreement is still
    good: a vendor switching negotiation off stops new haggling, but a deal they
    already shook hands on stays honoured until it expires on its own.
    """
    return bool(product.is_negotiable) and product.is_active and product.status == "active"


def max_negotiable_quantity(product: Product) -> int:
    """Upper bound on an offer's quantity: the vendor's cap, then stock."""
    cap = product.negotiation_max_quantity or product.stock or 1
    return max(1, min(int(cap), int(product.stock or 0) or int(cap)))


def validate_offer(
    product: Product,
    *,
    unit_price: int,
    quantity: int,
) -> None:
    """Reject an offer that could never become a valid order.

    The vendor's floor is checked here but never named in the error: telling a
    shopper "too low, the minimum is 420" hands them the vendor's private
    position, which is exactly what `min_acceptable_price` must not leak.
    """
    if unit_price <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter an offer above zero.",
        )
    if unit_price > product.price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That is above the listed price of {product.price}. Buy it at the listed price instead.",
        )
    if quantity < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose at least one unit.",
        )
    ceiling = max_negotiable_quantity(product)
    if quantity > ceiling:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"You can negotiate for up to {ceiling} of these at a time.",
        )


def offer_clears_floor(product: Product, unit_price: int) -> bool:
    """Whether an offer meets the vendor's private minimum.

    Used only to decide whether ODOS may auto-accept. An offer below the floor
    is still delivered to the vendor, who may take it anyway — the floor is a
    guide for automation, not a wall.
    """
    floor = product.min_acceptable_price
    return floor is None or unit_price >= int(floor)


def agreement_is_live(agreement: NegotiationAgreement, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    expires = agreement.expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return agreement.status == AgreementStatus.ACCEPTED.value and bool(expires and expires > current)


def expire_stale_agreements(db: Session, *, now: datetime | None = None) -> int:
    """Flip agreements whose deadline has passed. Returns how many moved.

    Called on read rather than by a scheduler so a stale row can never be seen
    as live, even on an instance where no background job is running.
    """
    current = now or datetime.now(UTC)
    rows = list(
        db.scalars(
            select(NegotiationAgreement).where(
                NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
                NegotiationAgreement.expires_at <= current,
            )
        ).all()
    )
    for row in rows:
        row.status = AgreementStatus.EXPIRED.value
        negotiation = db.get(PriceNegotiation, row.negotiation_id)
        if negotiation and negotiation.status == NegotiationStatus.AGREED.value:
            negotiation.status = NegotiationStatus.EXPIRED.value
    if rows:
        db.commit()
    return len(rows)


def live_agreement_for(
    db: Session,
    *,
    customer_user_id,
    product_id: str,
    selected_color: str | None = None,
    selected_size: str | None = None,
    now: datetime | None = None,
) -> NegotiationAgreement | None:
    """This shopper's usable agreement for this exact variant, if any.

    Looked up from the authenticated user and the product, never from anything
    the browser sent — which is what makes an agreement impossible to borrow,
    forge or point at a different product.
    """
    current = now or datetime.now(UTC)
    rows = list(
        db.scalars(
            select(NegotiationAgreement)
            .where(
                NegotiationAgreement.customer_user_id == customer_user_id,
                NegotiationAgreement.product_id == product_id,
                NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
                NegotiationAgreement.expires_at > current,
            )
            .order_by(NegotiationAgreement.accepted_at.desc())
        ).all()
    )
    for row in rows:
        # A deal struck on the red one does not buy the blue one.
        if (row.selected_color or None) != (selected_color or None):
            continue
        if (row.selected_size or None) != (selected_size or None):
            continue
        return row
    return None


def latest_pending_offer(negotiation: PriceNegotiation):
    for offer in reversed(negotiation.offers):
        if offer.status == OfferStatus.PENDING.value:
            return offer
    return None
