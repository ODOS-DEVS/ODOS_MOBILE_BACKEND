import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class VendorWallet(Base):
    __tablename__ = "vendor_wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    available_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    pending_withdrawal_balance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
        server_default="0",
    )
    lifetime_earnings: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    total_withdrawn: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    total_commission: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    payout_method_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payout_account_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_account_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payout_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_provider_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    paystack_recipient_code: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )
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

    user: Mapped["User"] = relationship(back_populates="vendor_wallet")
    transactions: Mapped[list["VendorWalletTransaction"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="VendorWalletTransaction.created_at.desc()",
    )
    withdrawal_requests: Mapped[list["VendorWithdrawalRequest"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="VendorWithdrawalRequest.created_at.desc()",
    )


class VendorWalletTransaction(Base):
    __tablename__ = "vendor_wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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
    withdrawal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor_withdrawal_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    gross_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    wallet: Mapped["VendorWallet"] = relationship(back_populates="transactions")
    vendor_user: Mapped["User"] = relationship(
        foreign_keys=[vendor_user_id],
        back_populates="vendor_wallet_transactions",
    )
    order: Mapped["Order | None"] = relationship()
    return_request: Mapped["ReturnRequest | None"] = relationship()
    withdrawal_request: Mapped["VendorWithdrawalRequest | None"] = relationship(
        foreign_keys=[withdrawal_request_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "vendor_user_id",
            "order_id",
            "kind",
            name="uq_vendor_wallet_tx_vendor_order_kind",
        ),
        UniqueConstraint(
            "vendor_user_id",
            "return_request_id",
            "kind",
            name="uq_vendor_wallet_tx_vendor_return_kind",
        ),
        UniqueConstraint(
            "vendor_user_id",
            "withdrawal_request_id",
            "kind",
            name="uq_vendor_wallet_tx_vendor_withdrawal_kind",
        ),
    )


class VendorWithdrawalRequest(Base):
    __tablename__ = "vendor_withdrawal_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payout_method_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payout_account_name: Mapped[str] = mapped_column(String(120), nullable=False)
    payout_account_number: Mapped[str] = mapped_column(String(80), nullable=False)
    payout_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_provider_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    paystack_recipient_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paystack_transfer_reference: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        unique=True,
    )
    paystack_transfer_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paystack_transfer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transfer_initiated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    transfer_failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    wallet: Mapped["VendorWallet"] = relationship(back_populates="withdrawal_requests")
    vendor_user: Mapped["User"] = relationship(
        foreign_keys=[vendor_user_id],
        back_populates="vendor_withdrawal_requests",
    )
    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by_user_id],
        back_populates="reviewed_vendor_withdrawal_requests",
    )
