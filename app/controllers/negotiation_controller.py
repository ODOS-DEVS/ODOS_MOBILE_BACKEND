"""The negotiation state machine.

Every transition is guarded by three questions in the same order: is the caller
a party to this negotiation, is the move legal from the current state, and does
the resulting price survive validation against the product row. A price that
arrived in a request body is never written anywhere without passing all three.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.models import Product, Store, User
from app.models.chat import ChatMessage, ChatThread, ChatThreadType
from app.models.negotiation import (
    ActorRole,
    AgreementStatus,
    NegotiationAgreement,
    NegotiationOffer,
    NegotiationStatus,
    OfferStatus,
    PriceNegotiation,
)
from app.schemas.negotiation import (
    NegotiationAgreementRead,
    NegotiationCountsRead,
    NegotiationOfferRead,
    NegotiationRead,
)
from app.services.negotiation_service import (
    agreement_expiry,
    agreement_reference,
    expire_stale_agreements,
    latest_pending_offer,
    negotiation_reference,
    product_accepts_negotiation,
    validate_offer,
)
from app.services.push_service import build_push_data, send_expo_push_notification

logger = logging.getLogger(__name__)

OPEN_STATUSES = {NegotiationStatus.OPEN.value, NegotiationStatus.AGREED.value}


# ---------------------------------------------------------------------------
# Loading and authorisation
# ---------------------------------------------------------------------------


def _load(db: Session, negotiation_id: uuid.UUID) -> PriceNegotiation:
    negotiation = db.scalar(
        select(PriceNegotiation)
        .options(selectinload(PriceNegotiation.offers))
        .where(PriceNegotiation.id == negotiation_id)
    )
    if not negotiation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negotiation not found.")
    return negotiation


def _role_of(negotiation: PriceNegotiation, user: User) -> str:
    """Which side the caller is on, or 404 if neither.

    A stranger gets "not found" rather than "forbidden": confirming that a
    negotiation exists is itself a leak about who is haggling over what.
    """
    if user.id == negotiation.customer_user_id:
        return ActorRole.CUSTOMER.value
    if user.id == negotiation.vendor_user_id:
        return ActorRole.VENDOR.value
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Negotiation not found.")


def _counterparty_id(negotiation: PriceNegotiation, actor_role: str) -> uuid.UUID:
    return (
        negotiation.vendor_user_id
        if actor_role == ActorRole.CUSTOMER.value
        else negotiation.customer_user_id
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _seconds_remaining(expires_at: datetime | None) -> int:
    if not expires_at:
        return 0
    stamped = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return max(0, int((stamped - datetime.now(UTC)).total_seconds()))


def _serialize_agreement(agreement: NegotiationAgreement | None) -> NegotiationAgreementRead | None:
    if not agreement:
        return None
    return NegotiationAgreementRead(
        id=agreement.id,
        reference=agreement.reference,
        negotiation_id=agreement.negotiation_id,
        product_id=agreement.product_id,
        listed_unit_price=agreement.listed_unit_price,
        agreed_unit_price=agreement.agreed_unit_price,
        quantity=agreement.quantity,
        currency=agreement.currency,
        status=agreement.status,
        accepted_at=agreement.accepted_at,
        expires_at=agreement.expires_at,
        seconds_remaining=_seconds_remaining(agreement.expires_at)
        if agreement.status == AgreementStatus.ACCEPTED.value
        else 0,
        order_id=agreement.order_id,
        order_number=agreement.order_number,
        used_at=agreement.used_at,
    )


def _awaiting(negotiation: PriceNegotiation) -> str | None:
    pending = latest_pending_offer(negotiation)
    if not pending:
        return None
    # Whoever did not make the pending offer owes the reply.
    return (
        ActorRole.VENDOR.value
        if pending.actor_role == ActorRole.CUSTOMER.value
        else ActorRole.CUSTOMER.value
    )


def serialize_negotiation(
    db: Session,
    negotiation: PriceNegotiation,
    *,
    include_history: bool = True,
) -> NegotiationRead:
    product = db.get(Product, negotiation.product_id)
    store = db.get(Store, negotiation.store_id)
    customer = db.get(User, negotiation.customer_user_id)
    agreement = db.scalar(
        select(NegotiationAgreement)
        .where(NegotiationAgreement.negotiation_id == negotiation.id)
        .order_by(NegotiationAgreement.created_at.desc())
    )
    pending = latest_pending_offer(negotiation)
    latest = pending or (negotiation.offers[-1] if negotiation.offers else None)

    return NegotiationRead(
        id=negotiation.id,
        reference=negotiation.reference,
        product_id=negotiation.product_id,
        product_title=product.title if product else negotiation.product_id,
        product_image_url=product.image_url if product else None,
        store_id=negotiation.store_id,
        store_name=store.title if store else None,
        vendor_user_id=negotiation.vendor_user_id,
        customer_user_id=negotiation.customer_user_id,
        customer_name=(customer.full_name or customer.email) if customer else None,
        thread_id=negotiation.thread_id,
        selected_color=negotiation.selected_color,
        selected_size=negotiation.selected_size,
        listed_unit_price=negotiation.listed_unit_price,
        current_listed_price=product.price if product else None,
        status=negotiation.status,
        awaiting=_awaiting(negotiation),
        latest_offer=NegotiationOfferRead.model_validate(latest) if latest else None,
        agreement=_serialize_agreement(agreement),
        offers=[NegotiationOfferRead.model_validate(o) for o in negotiation.offers]
        if include_history
        else [],
        created_at=negotiation.created_at,
        updated_at=negotiation.updated_at,
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def _notify(
    db: Session,
    *,
    recipient_id: uuid.UUID,
    negotiation: PriceNegotiation,
    kind: str,
    title: str,
    body: str,
    accent: str = "neutral",
    action_label: str = "View negotiation",
) -> None:
    """One notification per transition, routed at the negotiation itself.

    `route_target_id` is the negotiation id so both clients open the actual
    haggle rather than a generic inbox. A failure here is logged and swallowed:
    the state change is already committed and must not be undone because a push
    token was stale.
    """
    recipient = db.get(User, recipient_id)
    if not recipient:
        return
    try:
        event = create_notification_event(
            db,
            recipient,
            kind=kind,
            title=title,
            body=body,
            icon="pricetag-outline",
            accent=accent,
            action_label=action_label,
            route_type="negotiation",
            route_target_id=str(negotiation.id),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to record negotiation notification for %s", recipient_id)
        db.rollback()
        return

    if recipient.expo_push_token and recipient.allow_notifications:
        try:
            send_expo_push_notification(
                user=recipient,
                title=title,
                body=body,
                data=build_push_data(
                    push_type="negotiation",
                    route_type="negotiation",
                    route_target_id=str(negotiation.id),
                    notification_event=event,
                    extra={"negotiationId": str(negotiation.id)},
                ),
            )
        except Exception:
            logger.exception("Failed to push negotiation update to %s", recipient_id)


def _money(value: int, currency: str = "GHS") -> str:
    return f"{currency} {value:,}"


# ---------------------------------------------------------------------------
# Chat integration
# ---------------------------------------------------------------------------


def _ensure_thread(db: Session, negotiation: PriceNegotiation) -> ChatThread | None:
    """The pair's existing store conversation, created if they have never talked.

    Reuses `chat_threads` rather than opening a second inbox, so a negotiation
    appears in the conversation the two already have.
    """
    if negotiation.thread_id:
        thread = db.get(ChatThread, negotiation.thread_id)
        if thread:
            return thread

    thread = db.scalar(
        select(ChatThread).where(
            ChatThread.customer_user_id == negotiation.customer_user_id,
            ChatThread.store_id == negotiation.store_id,
            ChatThread.thread_type == ChatThreadType.STORE,
        )
    )
    if thread is None:
        thread = ChatThread(
            customer_user_id=negotiation.customer_user_id,
            vendor_user_id=negotiation.vendor_user_id,
            store_id=negotiation.store_id,
            thread_type=ChatThreadType.STORE,
        )
        db.add(thread)
        db.flush()
    negotiation.thread_id = thread.id
    return thread


def _post_offer_card(
    db: Session,
    negotiation: PriceNegotiation,
    offer: NegotiationOffer,
    *,
    body: str,
) -> None:
    """Drop an offer card into the conversation.

    `body` is a plain-text fallback for clients that do not know about offer
    cards — the mobile app renders it as an ordinary message today, so the
    sentence has to stand on its own.
    """
    # The offer must exist before a message can point at it. `chat_messages` has a plain
    # foreign key to `negotiation_offers` with no ORM relationship expressing it, so
    # SQLAlchemy has no dependency to order the two inserts by and will happily emit the
    # message first. That only fails once the pair already have a chat thread — the first
    # negotiation between them is saved by `_ensure_thread`'s own flush, every one after
    # that broke on the foreign key.
    db.flush()

    thread = _ensure_thread(db, negotiation)
    if not thread:
        return
    db.add(
        ChatMessage(
            thread_id=thread.id,
            sender_user_id=offer.created_by_user_id,
            recipient_user_id=_counterparty_id(negotiation, offer.actor_role),
            body=body,
            negotiation_offer_id=offer.id,
        )
    )


# ---------------------------------------------------------------------------
# Opening a negotiation
# ---------------------------------------------------------------------------


def start_negotiation(db: Session, user: User, payload) -> NegotiationRead:
    """A shopper's opening offer, which also creates the negotiation."""
    product = db.get(Product, payload.product_id)
    if not product or not product.is_active or product.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That product was not found.")
    if not product_accepts_negotiation(product):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The price of this product is not negotiable.",
        )
    if not product.store_id or not product.vendor_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This product is not set up for negotiation yet.",
        )
    if product.vendor_user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot negotiate on your own product.",
        )

    validate_offer(product, unit_price=payload.unit_price, quantity=payload.quantity)

    existing = db.scalar(
        select(PriceNegotiation).where(
            PriceNegotiation.customer_user_id == user.id,
            PriceNegotiation.product_id == product.id,
            PriceNegotiation.status == NegotiationStatus.OPEN.value,
        )
    )
    if existing and (existing.selected_color or None) == (payload.selected_color or None) and (
        existing.selected_size or None
    ) == (payload.selected_size or None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an open negotiation for this product.",
        )

    negotiation = PriceNegotiation(
        id=uuid.uuid4(),
        reference=negotiation_reference(),
        product_id=product.id,
        store_id=product.store_id,
        vendor_user_id=product.vendor_user_id,
        customer_user_id=user.id,
        selected_color=payload.selected_color,
        selected_size=payload.selected_size,
        listed_unit_price=int(product.price),
        status=NegotiationStatus.OPEN.value,
    )
    db.add(negotiation)
    db.flush()

    offer = NegotiationOffer(
        id=uuid.uuid4(),
        negotiation_id=negotiation.id,
        created_by_user_id=user.id,
        actor_role=ActorRole.CUSTOMER.value,
        unit_price=payload.unit_price,
        quantity=payload.quantity,
        message=(payload.message or "").strip() or None,
        status=OfferStatus.PENDING.value,
    )
    db.add(offer)
    _post_offer_card(
        db,
        negotiation,
        offer,
        body=f"Offer: {_money(payload.unit_price)} each for {payload.quantity} × {product.title}.",
    )

    try:
        db.commit()
    except IntegrityError:
        # The partial unique index caught a double submission or a race with
        # the shopper's own second tab.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an open negotiation for this product.",
        ) from None

    db.refresh(negotiation)
    _notify(
        db,
        recipient_id=negotiation.vendor_user_id,
        negotiation=negotiation,
        kind="negotiation_offer_received",
        title="New price offer",
        body=f"{user.full_name or 'A shopper'} offered {_money(payload.unit_price)} each for "
        f"{payload.quantity} × {product.title}.",
        accent="promo",
        action_label="Review offer",
    )
    return serialize_negotiation(db, _load(db, negotiation.id))


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------


def _require_open(negotiation: PriceNegotiation) -> None:
    if negotiation.status != NegotiationStatus.OPEN.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This negotiation is {negotiation.status} and can no longer be changed.",
        )


def _require_product(db: Session, negotiation: PriceNegotiation) -> Product:
    product = db.get(Product, negotiation.product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That product no longer exists.")
    return product


def counter_offer(db: Session, user: User, negotiation_id: uuid.UUID, payload) -> NegotiationRead:
    """Reply with a different price. Either side may counter; the pending offer
    they are answering is superseded rather than deleted."""
    negotiation = _load(db, negotiation_id)
    role = _role_of(negotiation, user)
    _require_open(negotiation)
    product = _require_product(db, negotiation)

    pending = latest_pending_offer(negotiation)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is nothing to respond to right now.",
        )
    if pending.actor_role == role:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your last offer is still awaiting a reply.",
        )

    quantity = payload.quantity or pending.quantity
    # A vendor countering above their own list price would be nonsense; a
    # shopper countering above it should just buy the thing.
    validate_offer(product, unit_price=payload.unit_price, quantity=quantity)

    pending.status = OfferStatus.SUPERSEDED.value
    pending.responded_at = datetime.now(UTC)
    # Flushed before the insert below. SQLAlchemy's unit of work emits INSERTs
    # ahead of UPDATEs, so without this the new pending row hits the
    # one-pending-offer index while the old row is still pending, and a
    # perfectly legal counteroffer fails as if it were a double submission.
    db.flush()

    offer = NegotiationOffer(
        id=uuid.uuid4(),
        negotiation_id=negotiation.id,
        created_by_user_id=user.id,
        actor_role=role,
        unit_price=payload.unit_price,
        quantity=quantity,
        message=(payload.message or "").strip() or None,
        status=OfferStatus.PENDING.value,
    )
    db.add(offer)
    _post_offer_card(
        db,
        negotiation,
        offer,
        body=f"Counteroffer: {_money(payload.unit_price)} each for {quantity} × {product.title}.",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your counteroffer was already sent.",
        ) from None

    negotiation = _load(db, negotiation_id)
    _notify(
        db,
        recipient_id=_counterparty_id(negotiation, role),
        negotiation=negotiation,
        kind="negotiation_counteroffer",
        title="Counteroffer received",
        body=f"{_money(payload.unit_price)} each for {quantity} × {product.title}.",
        accent="promo",
        action_label="Review counteroffer",
    )
    return serialize_negotiation(db, negotiation)


def accept_offer(db: Session, user: User, negotiation_id: uuid.UUID) -> NegotiationRead:
    """Take the other side's price. This is what creates the agreement.

    Only the party who did *not* make the pending offer may accept it —
    otherwise anyone could rubber-stamp their own proposal.
    """
    negotiation = _load(db, negotiation_id)
    role = _role_of(negotiation, user)
    _require_open(negotiation)
    product = _require_product(db, negotiation)

    pending = latest_pending_offer(negotiation)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="There is no offer to accept."
        )
    if pending.actor_role == role:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot accept your own offer.",
        )

    if product.status != "active" or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That product is no longer available, so this price cannot be agreed.",
        )
    if product.stock is not None and product.stock < pending.quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only {product.stock} left in stock, so {pending.quantity} cannot be agreed.",
        )

    now = datetime.now(UTC)
    pending.status = OfferStatus.ACCEPTED.value
    pending.responded_at = now
    negotiation.status = NegotiationStatus.AGREED.value

    agreement = NegotiationAgreement(
        id=uuid.uuid4(),
        reference=agreement_reference(),
        negotiation_id=negotiation.id,
        offer_id=pending.id,
        product_id=negotiation.product_id,
        vendor_user_id=negotiation.vendor_user_id,
        customer_user_id=negotiation.customer_user_id,
        selected_color=negotiation.selected_color,
        selected_size=negotiation.selected_size,
        # Frozen against the shelf price *now*, not when haggling opened: that
        # is the price the shopper is actually being saved from.
        listed_unit_price=int(product.price),
        agreed_unit_price=pending.unit_price,
        quantity=pending.quantity,
        status=AgreementStatus.ACCEPTED.value,
        accepted_at=now,
        expires_at=agreement_expiry(now),
    )
    db.add(agreement)
    db.commit()
    db.refresh(agreement)

    negotiation = _load(db, negotiation_id)
    total = agreement.agreed_unit_price * agreement.quantity
    # Both sides are told, because both need to know the clock is running.
    _notify(
        db,
        recipient_id=negotiation.customer_user_id,
        negotiation=negotiation,
        kind="negotiation_agreed",
        title="Your price was agreed",
        body=f"{product.title} at {_money(agreement.agreed_unit_price)} each "
        f"({_money(total)} total). Check out before it expires.",
        accent="success",
        action_label="Checkout at agreed price",
    )
    _notify(
        db,
        recipient_id=negotiation.vendor_user_id,
        negotiation=negotiation,
        kind="negotiation_agreed",
        title="Price agreed",
        body=f"{product.title} at {_money(agreement.agreed_unit_price)} each for "
        f"{agreement.quantity}. Awaiting the shopper's checkout.",
        accent="success",
    )
    return serialize_negotiation(db, negotiation)


def reject_offer(db: Session, user: User, negotiation_id: uuid.UUID, payload) -> NegotiationRead:
    """Turn the other side down and close the negotiation."""
    negotiation = _load(db, negotiation_id)
    role = _role_of(negotiation, user)
    _require_open(negotiation)

    pending = latest_pending_offer(negotiation)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="There is no offer to reject."
        )
    if pending.actor_role == role:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot reject your own offer — withdraw it instead.",
        )

    pending.status = OfferStatus.REJECTED.value
    pending.responded_at = datetime.now(UTC)
    negotiation.status = NegotiationStatus.REJECTED.value
    db.commit()

    negotiation = _load(db, negotiation_id)
    product = db.get(Product, negotiation.product_id)
    note = (payload.message or "").strip() if payload else ""
    _notify(
        db,
        recipient_id=_counterparty_id(negotiation, role),
        negotiation=negotiation,
        kind="negotiation_rejected",
        title="Offer declined",
        body=f"Your offer on {product.title if product else 'the product'} was declined."
        + (f" {note}" if note else ""),
    )
    return serialize_negotiation(db, negotiation)


def withdraw_offer(db: Session, user: User, negotiation_id: uuid.UUID) -> NegotiationRead:
    """Take back your own pending offer. Only the author may do this, which is
    what separates it from rejecting."""
    negotiation = _load(db, negotiation_id)
    role = _role_of(negotiation, user)
    _require_open(negotiation)

    pending = latest_pending_offer(negotiation)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You have no offer to withdraw."
        )
    if pending.actor_role != role:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That offer is not yours to withdraw.",
        )

    pending.status = OfferStatus.WITHDRAWN.value
    pending.responded_at = datetime.now(UTC)
    # The negotiation stays open: withdrawing is a step back, not a walkout, and
    # either side may still move.
    db.commit()
    return serialize_negotiation(db, _load(db, negotiation_id))


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_negotiations(
    db: Session,
    user: User,
    *,
    as_vendor: bool,
    status_filter: str | None = None,
) -> list[NegotiationRead]:
    """One side's negotiations. Scoped to the caller, so a vendor can never see
    another store's haggling and a shopper can never see someone else's."""
    expire_stale_agreements(db)

    column = (
        PriceNegotiation.vendor_user_id if as_vendor else PriceNegotiation.customer_user_id
    )
    query = (
        select(PriceNegotiation)
        .options(selectinload(PriceNegotiation.offers))
        .where(column == user.id)
        .order_by(PriceNegotiation.updated_at.desc())
    )
    rows = list(db.scalars(query).all())

    me = ActorRole.VENDOR.value if as_vendor else ActorRole.CUSTOMER.value
    serialized = [serialize_negotiation(db, row, include_history=False) for row in rows]

    cleaned = (status_filter or "all").strip().lower()
    if cleaned in {"", "all"}:
        return serialized
    if cleaned == "awaiting_me":
        return [n for n in serialized if n.awaiting == me]
    if cleaned == "awaiting_them":
        return [n for n in serialized if n.awaiting and n.awaiting != me]
    return [n for n in serialized if n.status == cleaned]


def negotiation_counts(db: Session, user: User, *, as_vendor: bool) -> NegotiationCountsRead:
    rows = list_negotiations(db, user, as_vendor=as_vendor)
    me = ActorRole.VENDOR.value if as_vendor else ActorRole.CUSTOMER.value
    return NegotiationCountsRead(
        total=len(rows),
        awaiting_me=sum(1 for n in rows if n.awaiting == me),
        awaiting_them=sum(1 for n in rows if n.awaiting and n.awaiting != me),
        agreed=sum(1 for n in rows if n.status == NegotiationStatus.AGREED.value),
        completed=sum(1 for n in rows if n.status == NegotiationStatus.COMPLETED.value),
        rejected=sum(1 for n in rows if n.status == NegotiationStatus.REJECTED.value),
        expired=sum(1 for n in rows if n.status == NegotiationStatus.EXPIRED.value),
    )


def get_negotiation(db: Session, user: User, negotiation_id: uuid.UUID) -> NegotiationRead:
    expire_stale_agreements(db)
    negotiation = _load(db, negotiation_id)
    _role_of(negotiation, user)
    return serialize_negotiation(db, negotiation)
