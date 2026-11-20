"""Price negotiation: the conversation, its offers, and the agreement it produces.

Three tables with one job between them — let a shopper and a vendor settle on a
price for one product, and let exactly that shopper buy exactly that product at
exactly that price, once, before it expires.

Offers are rows rather than chat text because the state machine has to be
enforceable and a dispute has to be reconstructable. The agreement is separate
from the offer that produced it because an offer is a proposal and an agreement
is a commitment with its own lifetime and its own consumption.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NegotiationStatus(str, enum.Enum):
    OPEN = "open"
    AGREED = "agreed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    COMPLETED = "completed"


class OfferStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    #: Replaced by a counteroffer. Kept rather than deleted so the history reads
    #: as the back-and-forth it actually was.
    SUPERSEDED = "superseded"


class AgreementStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    USED = "used"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ActorRole(str, enum.Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"


class PriceNegotiation(Base):
    """One shopper haggling with one vendor over one product variant."""

    __tablename__ = "price_negotiations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The pair's existing store conversation, so offers appear where they already talk.
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True
    )
    selected_color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    selected_size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Shelf price when the haggling started, for "down from" without re-reading the product.
    listed_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    offers: Mapped[list[NegotiationOffer]] = relationship(
        back_populates="negotiation",
        cascade="all, delete-orphan",
        order_by="NegotiationOffer.created_at",
    )


class NegotiationOffer(Base):
    """One move in the exchange: an opening offer or a counter.

    A partial unique index allows only one `pending` row per negotiation, which
    is what makes a double-tapped send a no-op instead of two live offers.
    """

    __tablename__ = "negotiation_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(10), nullable=False)
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    negotiation: Mapped[PriceNegotiation] = relationship(back_populates="offers")


class NegotiationAgreement(Base):
    """A settled price, private to one shopper, good for one order, with a deadline.

    The only record checkout trusts. Separate from the accepted offer because an
    offer is a proposal and this is a commitment: it has its own expiry, its own
    status, and it is consumed exactly once — enforced by a unique index on
    `order_id` rather than by a check that two racing checkouts could both pass.
    """

    __tablename__ = "negotiation_agreements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    negotiation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_offers.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selected_color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    selected_size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Shelf price at the moment of agreement. Frozen: a later reprice must not
    #: rewrite what was agreed, nor the saving the shopper was shown.
    listed_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    agreed_unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="GHS", server_default="GHS")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="accepted", server_default="accepted", index=True
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
