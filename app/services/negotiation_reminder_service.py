"""Warn before an agreed price lapses, and tell both sides when it has.

An agreement is the shopper's half of a bargain they already struck, and it dies
silently on a timer. Two nudges make that fair: one while there is still time to
act, and one confirming it has gone so neither party is left assuming a deal is
still live.

Idempotency is carried by the agreement's own status rather than by a sent-log.
The warning uses a short-lived marker on `used_at`'s sibling field — see
`_claim_warning` — so a job that runs every ten minutes does not send ten
warnings an hour.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Product, User
from app.models.negotiation import (
    AgreementStatus,
    NegotiationAgreement,
    NegotiationStatus,
    PriceNegotiation,
)

logger = logging.getLogger(__name__)

#: How close to the deadline the warning goes out.
WARNING_WINDOW = timedelta(hours=3)

NEGOTIATION_REMINDER_INTERVAL_SECONDS = 600


def _notify(db: Session, user_id, *, negotiation_id, kind, title, body, accent="neutral"):
    from app.controllers.notification_controller import create_notification_event

    user = db.get(User, user_id)
    if not user:
        return
    try:
        create_notification_event(
            db,
            user,
            kind=kind,
            title=title,
            body=body,
            icon="pricetag-outline",
            accent=accent,
            action_label="View negotiation",
            route_type="negotiation",
            route_target_id=str(negotiation_id),
        )
    except Exception:
        logger.exception("Failed to record negotiation reminder for %s", user_id)


def _claim_warning(db: Session, agreement_id, now: datetime) -> bool:
    """Stamp `expiry_warned_at` and report whether this caller was the one to do it.

    A conditional update rather than a read-then-write: two workers ticking at
    the same moment both reach here, and only one row matches.
    """
    result = db.execute(
        update(NegotiationAgreement)
        .where(
            NegotiationAgreement.id == agreement_id,
            NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
            NegotiationAgreement.expiry_warned_at.is_(None),
        )
        .values(expiry_warned_at=now)
    )
    return result.rowcount == 1


def process_negotiation_agreement_reminders() -> None:
    """Warn on agreements nearing expiry, and close out those that have passed."""
    db = SessionLocal()
    try:
        now = datetime.now(UTC)

        # --- Expiring soon -------------------------------------------------
        soon = list(
            db.scalars(
                select(NegotiationAgreement).where(
                    NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
                    NegotiationAgreement.expiry_warned_at.is_(None),
                    NegotiationAgreement.expires_at > now,
                    NegotiationAgreement.expires_at <= now + WARNING_WINDOW,
                )
            ).all()
        )
        warned = 0
        for agreement in soon:
            if not _claim_warning(db, agreement.id, now):
                continue
            db.commit()
            product = db.get(Product, agreement.product_id)
            name = product.title if product else "your item"
            hours = max(1, int((agreement.expires_at.replace(tzinfo=UTC) - now).total_seconds() // 3600))
            _notify(
                db,
                agreement.customer_user_id,
                negotiation_id=agreement.negotiation_id,
                kind="negotiation_agreement_expiring",
                title="Your agreed price is about to expire",
                body=f"{name} at GHS {agreement.agreed_unit_price:,} expires in about {hours}h. "
                "Check out to keep it.",
                accent="warning",
            )
            _notify(
                db,
                agreement.vendor_user_id,
                negotiation_id=agreement.negotiation_id,
                kind="negotiation_agreement_expiring",
                title="An agreed price is about to expire",
                body=f"{name} at GHS {agreement.agreed_unit_price:,} expires in about {hours}h "
                "and has not been bought yet.",
            )
            db.commit()
            warned += 1

        # --- Already lapsed -------------------------------------------------
        lapsed = list(
            db.scalars(
                select(NegotiationAgreement).where(
                    NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
                    NegotiationAgreement.expires_at <= now,
                )
            ).all()
        )
        expired = 0
        for agreement in lapsed:
            result = db.execute(
                update(NegotiationAgreement)
                .where(
                    NegotiationAgreement.id == agreement.id,
                    NegotiationAgreement.status == AgreementStatus.ACCEPTED.value,
                )
                .values(status=AgreementStatus.EXPIRED.value)
            )
            if result.rowcount != 1:
                continue
            negotiation = db.get(PriceNegotiation, agreement.negotiation_id)
            if negotiation and negotiation.status == NegotiationStatus.AGREED.value:
                negotiation.status = NegotiationStatus.EXPIRED.value
            db.commit()

            product = db.get(Product, agreement.product_id)
            name = product.title if product else "your item"
            _notify(
                db,
                agreement.customer_user_id,
                negotiation_id=agreement.negotiation_id,
                kind="negotiation_agreement_expired",
                title="Your agreed price expired",
                body=f"The agreed price for {name} has lapsed. You can make the seller a new offer.",
            )
            _notify(
                db,
                agreement.vendor_user_id,
                negotiation_id=agreement.negotiation_id,
                kind="negotiation_agreement_expired",
                title="An agreed price expired",
                body=f"The agreed price for {name} lapsed before the buyer checked out.",
            )
            db.commit()
            expired += 1

        if warned or expired:
            logger.info(
                "Negotiation reminders: warned %s, expired %s", warned, expired
            )
    finally:
        db.close()
