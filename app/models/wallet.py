import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
    text,
)
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
        # NOT scoped to kind='refund_reversal' — an order can have several
        # items each refunded independently, so "one refund_reversal row per
        # order" would reject every reversal on that order after the first.
        # That kind is deduped by return_request_id instead, via the
        # (vendor_user_id, return_request_id, kind) constraint below.
        Index(
            "uq_vendor_wallet_tx_vendor_order_kind",
            "vendor_user_id",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=text("kind != 'refund_reversal'"),
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


class CustomerWallet(Base):
    __tablename__ = "customer_wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    available_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    lifetime_topups: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    lifetime_spend: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    lifetime_refunds: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
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

    user: Mapped["User"] = relationship(back_populates="customer_wallet")
    transactions: Mapped[list["CustomerWalletTransaction"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="CustomerWalletTransaction.created_at.desc()",
    )
    topups: Mapped[list["CustomerWalletTopUp"]] = relationship(
        back_populates="wallet",
        cascade="all, delete-orphan",
        order_by="CustomerWalletTopUp.created_at.desc()",
    )


class CustomerWalletTransaction(Base):
    __tablename__ = "customer_wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
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
    topup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_wallet_topups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    return_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    wallet: Mapped["CustomerWallet"] = relationship(back_populates="transactions")
    user: Mapped["User"] = relationship(
        foreign_keys=[user_id],
        back_populates="customer_wallet_transactions",
    )
    order: Mapped["Order | None"] = relationship()
    topup: Mapped["CustomerWalletTopUp | None"] = relationship(back_populates="transaction")
    return_request: Mapped["ReturnRequest | None"] = relationship()

    __table_args__ = (
        # NOT scoped to kind='credit_return' — an order can have several
        # items each refunded via a separate return request, so "one
        # credit_return row per order" would reject every refund on that
        # order after the first. That kind is deduped by return_request_id
        # instead, via the partial index below.
        Index(
            "uq_customer_wallet_tx_user_order_kind",
            "user_id",
            "order_id",
            "kind",
            unique=True,
            postgresql_where=text("kind != 'credit_return'"),
        ),
        UniqueConstraint(
            "user_id",
            "topup_id",
            "kind",
            name="uq_customer_wallet_tx_user_topup_kind",
        ),
        Index(
            "uq_customer_wallet_tx_return_request",
            "return_request_id",
            unique=True,
            postgresql_where=text("kind = 'credit_return' AND return_request_id IS NOT NULL"),
        ),
    )


class CustomerWalletTopUp(Base):
    __tablename__ = "customer_wallet_topups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_wallets.id", ondelete="CASCADE"),
        nullable=False,
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
    amount_subunit: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    provider_transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    gateway_response: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    wallet: Mapped["CustomerWallet"] = relationship(back_populates="topups")
    user: Mapped["User"] = relationship(
        foreign_keys=[user_id],
        back_populates="customer_wallet_topups",
    )
    transaction: Mapped["CustomerWalletTransaction | None"] = relationship(
        back_populates="topup",
        uselist=False,
    )
