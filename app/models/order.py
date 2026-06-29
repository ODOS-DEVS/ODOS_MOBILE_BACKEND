import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="buy_now")
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_payment",
        server_default="pending_payment",
        index=True,
    )
    vendor_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    subtotal_amount: Mapped[float] = mapped_column(Float, nullable=False)
    shipping_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    delivery_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="economy",
        server_default="economy",
    )
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    tracking_eta: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    address_full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    address_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    address_street: Mapped[str] = mapped_column(String(255), nullable=False)
    address_city: Mapped[str] = mapped_column(String(120), nullable=False)
    address_region: Mapped[str] = mapped_column(String(120), nullable=False)

    payment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payment_label: Mapped[str] = mapped_column(String(120), nullable=False)
    payment_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    payment_provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="paystack",
        server_default="paystack",
        index=True,
    )
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    payment_network: Mapped[str | None] = mapped_column(String(60), nullable=True)
    payment_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payment_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    voucher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    voucher_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    voucher_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    discount_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")

    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    user: Mapped["User"] = relationship(back_populates="orders")
    payment_transaction: Mapped["PaymentTransaction | None"] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        uselist=False,
    )
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.created_at.asc()",
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="Review.updated_at.desc()",
    )
    return_requests: Mapped[list["ReturnRequest"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="ReturnRequest.created_at.desc()",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    store_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    line_total: Mapped[float] = mapped_column(Float, nullable=False)
    is_returnable: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    selected_color: Mapped[str | None] = mapped_column(String(60), nullable=True)
    selected_size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    order: Mapped["Order"] = relationship(back_populates="items")
    return_requests: Mapped[list["ReturnRequest"]] = relationship(
        back_populates="order_item",
        cascade="all, delete-orphan",
        order_by="ReturnRequest.created_at.desc()",
    )


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="requested",
        server_default="requested",
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evidence_image_urls: Mapped[list[str] | None] = mapped_column(ARRAY(String(500)), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    order: Mapped["Order"] = relationship(back_populates="return_requests")
    order_item: Mapped["OrderItem"] = relationship(back_populates="return_requests")
    user: Mapped["User"] = relationship(
        foreign_keys=[user_id],
        back_populates="return_requests",
    )
    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by_user_id],
        back_populates="reviewed_return_requests",
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
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
    )
    product_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str] = mapped_column(String(500), nullable=False)
    is_hidden: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    moderation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    moderated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
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

    user: Mapped["User"] = relationship(
        back_populates="reviews",
        foreign_keys=[user_id],
    )
    moderated_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[moderated_by_user_id],
        back_populates="moderated_reviews",
    )
    order: Mapped["Order"] = relationship(back_populates="reviews")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "order_id",
            "product_id",
            name="uq_reviews_user_order_product",
        ),
    )
