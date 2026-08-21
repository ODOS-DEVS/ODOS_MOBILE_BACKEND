"""Loyalty rewards system models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class LoyaltyAccount(Base):
    """Customer loyalty account tracking points and tier."""

    __tablename__ = "loyalty_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    total_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tier_level: Mapped[str] = mapped_column(
        String(20),
        default="bronze",
        server_default="bronze",
        nullable=False,
    )
    tier_progress_percent: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    lifetime_spend: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default="0.0",
        nullable=False,
    )
    tier_upgraded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    transactions: Mapped[list["LoyaltyTransaction"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="LoyaltyTransaction.created_at.desc()",
    )

    __table_args__ = (
        {"indexes": [{"name": "idx_loyalty_tier_level", "column_names": ["tier_level"]}]},
    )


class LoyaltyTransaction(Base):
    """Individual loyalty points transactions."""

    __tablename__ = "loyalty_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loyalty_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    points_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata: Mapped[dict | None] = mapped_column(
        default=None,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    account: Mapped["LoyaltyAccount"] = relationship(back_populates="transactions")

    __table_args__ = (
        {
            "indexes": [
                {"name": "idx_loyalty_type_created", "column_names": ["transaction_type", "created_at"]},
            ]
        },
    )


class LoyaltyTierBenefit(Base):
    """Tier benefits configuration (admin-configurable)."""

    __tablename__ = "loyalty_tier_benefits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tier_name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    tier_order: Mapped[int] = mapped_column(Integer, default=0)
    min_spend: Mapped[float] = mapped_column(Float, nullable=False)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)
    points_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    free_shipping_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    birthday_bonus_points: Mapped[int] = mapped_column(Integer, default=0)
    exclusive_deals_access: Mapped[bool] = mapped_column(default=False)
    priority_support: Mapped[bool] = mapped_column(default=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
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
