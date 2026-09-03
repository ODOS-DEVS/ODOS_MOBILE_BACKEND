"""Couriers: the first ODOS-modeled delivery actor.

Today's delivery model is explicit that no such actor exists yet --
delivery_lifecycle_service.py: "Vendor -> vendor's own (external) delivery
rider -> Customer. There is no rider app." Everything here follows the
vendor/VendorApplication/VendorWallet pattern deliberately, because that
pattern is already proven under concurrency (settlement idempotency, row
locking, ledger reconciliation) and a second, differently-shaped money path
for courier pay would be the mistake -- not a design choice.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CourierStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class VehicleType(str, enum.Enum):
    ON_FOOT = "on_foot"
    BIKE = "bike"
    MOTORBIKE = "motorbike"
    CAR = "car"
    VAN = "van"


class CourierApplication(Base):
    """Application history. Mirrors VendorApplication exactly -- one row per
    submission, reviewed by an admin, independent of the operational profile
    that only exists once approved."""

    __tablename__ = "courier_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[CourierStatus] = mapped_column(
        Enum(
            CourierStatus,
            name="courier_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=CourierStatus.PENDING,
        server_default=CourierStatus.PENDING.value,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False)
    # Set only when this application is for one vendor's dedicated fleet
    # rather than ODOS's open pool.
    vendor_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vehicle_type: Mapped[VehicleType] = mapped_column(
        Enum(
            VehicleType,
            name="courier_vehicle_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    plate_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ghana_card_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    id_document_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by_user_id]
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_courier_applications_user_id"),
    )


class Courier(Base):
    """The operational profile, created once an application is approved --
    the same relationship VendorApplication has to Store.

    Carries the *current* position only, not a location history. A live map
    needs "where are they now"; a history table is a deliberate later
    addition (see the design doc), not a foundation piece.
    """

    __tablename__ = "couriers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    # Null = ODOS's open pool. Set = dedicated to one vendor's own fleet.
    vendor_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vehicle_type: Mapped[VehicleType] = mapped_column(
        Enum(
            VehicleType,
            name="courier_vehicle_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    plate_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_online: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    current_latitude: Mapped[float | None] = mapped_column(nullable=True)
    current_longitude: Mapped[float | None] = mapped_column(nullable=True)
    location_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_deliveries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    wallet: Mapped["CourierWallet | None"] = relationship(
        back_populates="courier", uselist=False
    )


class DeliveryOffer(Base):
    """The claim pool. One row per order once the vendor marks it ready for
    pickup.

    Claiming is a SELECT ... FOR UPDATE SKIP LOCKED against this row: the
    first courier to grab the lock wins, everyone else sees it as already
    gone rather than waiting on the winner's transaction. That distinction
    matters -- FOR UPDATE alone would make a losing courier's app appear to
    hang rather than showing the offer as taken.
    """

    __tablename__ = "delivery_offers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Scopes visibility: set means only that vendor's own couriers may see or
    # claim it. Null means ODOS's open pool.
    vendor_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open", index=True
    )
    claimed_by_courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_by_admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Watched by the same admin-ops SLA sweep pattern that already exists for
    # delivery stage timeouts -- unclaimed past this is flagged for admin,
    # never left to sit indefinitely.
    sla_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship()
    claimed_by_courier: Mapped["Courier | None"] = relationship()

    __table_args__ = (
        # One active offer per order -- a second offer for an order that
        # already has one open would let two couriers claim the same delivery
        # through two different rows.
        UniqueConstraint("order_id", name="uq_delivery_offers_order_id"),
        Index("ix_delivery_offers_status_vendor", "status", "vendor_id"),
    )


class CourierWallet(Base):
    """Identical shape to VendorWallet, deliberately. Courier pay comes out
    of shipping_amount, which finance_math.py never allocates to anyone
    today -- ODOS keeps all of it. This is a new outflow from money already
    collected, booked through the same ledger, not a parallel system."""

    __tablename__ = "courier_wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="GHS", server_default="GHS"
    )
    available_balance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    pending_withdrawal_balance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    lifetime_earnings: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    total_withdrawn: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    payout_method_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payout_account_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_account_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payout_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_provider_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    paystack_recipient_code: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    courier: Mapped["Courier"] = relationship(back_populates="wallet")
    transactions: Mapped[list["CourierWalletTransaction"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="CourierWalletTransaction.created_at.desc()",
    )
    withdrawal_requests: Mapped[list["CourierWithdrawalRequest"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="CourierWithdrawalRequest.created_at.desc()",
    )


class CourierWalletTransaction(Base):
    __tablename__ = "courier_wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courier_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    withdrawal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courier_withdrawal_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    wallet: Mapped["CourierWallet"] = relationship(back_populates="transactions")
    courier: Mapped["Courier"] = relationship()
    order: Mapped["Order | None"] = relationship()
    withdrawal_request: Mapped["CourierWithdrawalRequest | None"] = relationship(
        foreign_keys=[withdrawal_request_id],
    )

    __table_args__ = (
        # One delivery-fee credit per order per courier -- the same shape as
        # the vendor settlement guard, and for the same reason: this is what
        # actually stops a duplicate settlement attempt from double-paying,
        # not just the "check for an existing row first" application code.
        Index(
            "uq_courier_wallet_tx_courier_order_kind",
            "courier_id",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
        UniqueConstraint(
            "courier_id",
            "withdrawal_request_id",
            "kind",
            name="uq_courier_wallet_tx_courier_withdrawal_kind",
        ),
    )


class CourierWithdrawalRequest(Base):
    """Identical shape to VendorWithdrawalRequest."""

    __tablename__ = "courier_withdrawal_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courier_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payout_method_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payout_account_name: Mapped[str] = mapped_column(String(120), nullable=False)
    payout_account_number: Mapped[str] = mapped_column(String(80), nullable=False)
    payout_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_provider_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    paystack_recipient_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paystack_transfer_reference: Mapped[str | None] = mapped_column(
        String(80), nullable=True, unique=True
    )
    paystack_transfer_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paystack_transfer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transfer_initiated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transfer_failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    wallet: Mapped["CourierWallet"] = relationship(back_populates="withdrawal_requests")
    courier: Mapped["Courier"] = relationship()
    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by_user_id]
    )
