import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PlatformTreasuryAccount(Base):
    __tablename__ = "platform_treasury_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="GHS",
        server_default="GHS",
        unique=True,
        index=True,
    )
    current_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    vendor_liability_balance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
        server_default="0",
    )
    commission_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    gross_collected_total: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    processor_fee_total: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    refunded_total: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    total_payouts_sent: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    ledger_entries: Mapped[list["PlatformLedgerEntry"]] = relationship(
        back_populates="treasury_account",
        cascade="all, delete-orphan",
        order_by="PlatformLedgerEntry.created_at.desc()",
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="paystack",
        server_default="paystack",
        index=True,
    )
    reference: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    access_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    authorization_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    amount_subunit: Mapped[int] = mapped_column(Integer, nullable=False)
    processor_fee_subunit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="initialized",
        server_default="initialized",
        index=True,
    )
    preferred_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    gateway_response: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    order: Mapped["Order"] = relationship(back_populates="payment_transaction")
    user: Mapped["User"] = relationship(back_populates="payment_transactions")
    ledger_entries: Mapped[list["PlatformLedgerEntry"]] = relationship(
        back_populates="payment_transaction",
        order_by="PlatformLedgerEntry.created_at.desc()",
    )


class PlatformLedgerEntry(Base):
    __tablename__ = "platform_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    treasury_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_treasury_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    return_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vendor_withdrawal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor_withdrawal_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    current_balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    vendor_liability_balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    commission_balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    treasury_account: Mapped["PlatformTreasuryAccount"] = relationship(back_populates="ledger_entries")
    payment_transaction: Mapped["PaymentTransaction | None"] = relationship(back_populates="ledger_entries")
    order: Mapped["Order | None"] = relationship()
    return_request: Mapped["ReturnRequest | None"] = relationship()
    vendor_withdrawal_request: Mapped["VendorWithdrawalRequest | None"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "payment_transaction_id",
            "kind",
            name="uq_platform_ledger_payment_kind",
        ),
        UniqueConstraint(
            "return_request_id",
            "kind",
            name="uq_platform_ledger_return_kind",
        ),
        UniqueConstraint(
            "vendor_withdrawal_request_id",
            "kind",
            name="uq_platform_ledger_withdrawal_kind",
        ),
    )


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    event_digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    signature: Mapped[str | None] = mapped_column(String(160), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="received",
        server_default="received",
        index=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
