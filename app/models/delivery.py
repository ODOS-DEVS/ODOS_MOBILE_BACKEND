"""The delivery (fulfilment) entity.

Why this exists as its own table rather than more columns on Order:

An Order is a commercial fact -- who bought what, for how much. A Delivery is
an operational one -- who is carrying it, how far along they are, and what
happened on each attempt. Keeping them separate is what makes a second attempt,
a re-offer after an abandoned claim, and (later) one rider carrying several
orders expressible at all. The previous shape could not express any of them:
delivery_offers had UniqueConstraint("order_id"), so an order could be offered
exactly once, ever.

Order.courier_id / courier_assigned_at / courier_picked_up_at stay exactly
where they are and keep being written. They are a denormalized read path for
the vendor and admin views that already query them, and nothing that reads
them today needs to learn about this table.

Status is String(30) with a Python enum for validation, deliberately not a
native Postgres enum. This codebase has already been bitten once by
ALTER TYPE ... ADD VALUE (see c0ur13rr0l3, which existed only to add a single
label to user_role), and a delivery lifecycle is going to gain states.
Order.delivery_status made the same call for the same reason.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Imported for type checking only. SQLAlchemy resolves these relationship
# targets from the string annotations at mapper-configuration time, so a
# runtime import would be both unnecessary and circular. Declaring them here
# gives type checkers and editors the real types without that cost.
if TYPE_CHECKING:
    from app.models.courier import Courier
    from app.models.order import Order


class DeliveryStatus(str, enum.Enum):
    """Every state a delivery can be in. Transitions between them are not
    free-form -- see app/services/delivery_state_machine.py, which owns the
    only table of legal moves."""

    PENDING_ASSIGNMENT = "pending_assignment"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    EN_ROUTE_TO_PICKUP = "en_route_to_pickup"
    ARRIVED_AT_PICKUP = "arrived_at_pickup"
    PICKUP_VERIFICATION = "pickup_verification"
    PICKED_UP = "picked_up"
    EN_ROUTE_TO_CUSTOMER = "en_route_to_customer"
    ARRIVED_AT_CUSTOMER = "arrived_at_customer"
    DELIVERY_VERIFICATION = "delivery_verification"
    DELIVERED = "delivered"
    # Exceptional paths
    CUSTOMER_UNAVAILABLE = "customer_unavailable"
    DELIVERY_FAILED = "delivery_failed"
    RETURN_REQUIRED = "return_required"
    RETURNING_TO_VENDOR = "returning_to_vendor"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"


#: States in which a rider is actively holding the delivery. Used to enforce
#: "one active delivery per rider" and to decide whether live location is
#: legitimate to accept (see §20 -- no tracking outside an active delivery).
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStatus.ACCEPTED.value,
        DeliveryStatus.EN_ROUTE_TO_PICKUP.value,
        DeliveryStatus.ARRIVED_AT_PICKUP.value,
        DeliveryStatus.PICKUP_VERIFICATION.value,
        DeliveryStatus.PICKED_UP.value,
        DeliveryStatus.EN_ROUTE_TO_CUSTOMER.value,
        DeliveryStatus.ARRIVED_AT_CUSTOMER.value,
        DeliveryStatus.DELIVERY_VERIFICATION.value,
        DeliveryStatus.CUSTOMER_UNAVAILABLE.value,
        DeliveryStatus.RETURN_REQUIRED.value,
        DeliveryStatus.RETURNING_TO_VENDOR.value,
    }
)

#: Nothing more will happen to a delivery in one of these.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryStatus.DELIVERED.value,
        DeliveryStatus.RETURNED.value,
        DeliveryStatus.CANCELLED.value,
        DeliveryStatus.DISPUTED.value,
    }
)


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The store this leg collects from. Null only if the store row is later
    # deleted; a delivery always starts life with one.
    vendor_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True, index=True
    )
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DeliveryStatus.PENDING_ASSIGNMENT.value,
        server_default=DeliveryStatus.PENDING_ASSIGNMENT.value,
        index=True,
    )

    # --- Pickup snapshot -------------------------------------------------
    # Snapshotted at creation rather than joined at read time: a store can
    # rename itself or move, and a delivery record should say where the rider
    # was actually sent.
    pickup_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pickup_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    pickup_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    pickup_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Drop-off snapshot ----------------------------------------------
    # Two fields on purpose. `dropoff_area` is city/region only and is what a
    # rider may see *before* claiming; `dropoff_address` is the full street
    # address and is released only to the rider who holds the delivery. Storing
    # them separately is what makes that boundary enforceable server-side
    # instead of being a formatting decision in the client.
    dropoff_area: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dropoff_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    dropoff_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    dropoff_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Progress --------------------------------------------------------
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    failure_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at_pickup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at_customer_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship()
    courier: Mapped[Courier | None] = relationship()
    events: Mapped[list[DeliveryEvent]] = relationship(
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="DeliveryEvent.occurred_at.asc()",
    )
    attempts: Mapped[list[DeliveryAttempt]] = relationship(
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="DeliveryAttempt.attempt_number.asc()",
    )

    __table_args__ = (
        # One live delivery per order. Re-attempts reuse the row; a second row
        # would let an order be carried by two riders at once. Terminal rows
        # are excluded so an order that failed and was re-created is still
        # possible later without dropping this guarantee.
        Index(
            "uq_deliveries_active_order",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('delivered', 'returned', 'cancelled', 'disputed')"
            ),
        ),
        Index("ix_deliveries_courier_status", "courier_id", "status"),
    )


class DeliveryEvent(Base):
    """Append-only audit trail. Every status change writes one, with the actor
    that caused it.

    Modelled on OrderStatusEvent, which already powers the customer-facing
    order timeline. Never stores a verification code or any other secret --
    `context` is for ids and reasons.
    """

    __tablename__ = "delivery_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # courier | vendor | customer | admin | system
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    delivery: Mapped[Delivery] = relationship(back_populates="events")


class DeliveryAttempt(Base):
    """One try at handing the order to the customer.

    A delivery can need more than one (customer unavailable, wrong location).
    Verification in Phase 3 binds to an attempt rather than to the order, so a
    code from a failed attempt can never complete a later one.
    """

    __tablename__ = "delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # in_progress | succeeded | failed
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress", server_default="in_progress"
    )
    failure_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    delivery: Mapped[Delivery] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_delivery_attempts_delivery_number"
        ),
    )
