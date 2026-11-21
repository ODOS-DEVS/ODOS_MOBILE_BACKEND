"""The refund ledger.

A row per attempt to return money, not a flag on the order. KutanaPay exposes
no refund endpoint, so the money moves by hand and this records who confirmed
it and against what reference — which is the only evidence the transfer
happened at all.

Several rows per order are expected: a multi-vendor order can lose one vendor's
part today and another's next week, and each is its own refund with its own
outcome. A partial unique index keeps only one of them *open* per scope at a
time, so a double-tapped button cannot produce two.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OrderRefund(Base):
    __tablename__ = "order_refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Null for a whole-order refund; set when only one vendor's part is going
    #: back, which is what makes partial multi-vendor cancellation possible.
    vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="GHS", server_default="GHS")

    #: How the number was reached, frozen at the time. Prices move; a refund
    #: query six months later has to be answerable without re-deriving it.
    items_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    delivery_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    discount_withheld: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    gateway_fee_withheld: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Mandatory before a refund may be called successful.
    provider_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
