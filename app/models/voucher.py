import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Voucher(Base):
    __tablename__ = "vouchers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issuer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="odos",
        server_default="odos",
        index=True,
    )
    owner_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="platform",
        server_default="platform",
        index=True,
    )
    availability: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="auto",
        server_default="auto",
        index=True,
    )
    store_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    eligible_store_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)),
        nullable=True,
    )
    reward_text: Mapped[str] = mapped_column(String(80), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)
    discount_value: Mapped[float] = mapped_column(Float, nullable=False)
    min_subtotal: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    max_discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_user_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    campaign_tag: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="public",
        server_default="public",
    )
    approval_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="approved",
        server_default="approved",
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_order_only: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    new_user_only: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    category_slugs: Mapped[list[str] | None] = mapped_column(ARRAY(String(80)), nullable=True)
    excluded_category_slugs: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(80)),
        nullable=True,
    )
    product_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)), nullable=True)
    excluded_product_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)), nullable=True)
    promotion_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="coupon",
        server_default="coupon",
        index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        index=True,
    )
    stackable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    exclusive_group: Mapped[str | None] = mapped_column(String(50), nullable=True)
    auto_apply: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )
    bogo_buy_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bogo_get_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bogo_get_discount_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    rules_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class VoucherRedemption(Base):
    __tablename__ = "voucher_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    voucher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )
    voucher_code: Mapped[str] = mapped_column(String(40), nullable=False)
    discount_amount: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class VoucherAssignment(Base):
    __tablename__ = "voucher_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    voucher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="claim",
        server_default="claim",
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("voucher_id", "user_id", name="uq_voucher_assignments_voucher_user"),
    )
