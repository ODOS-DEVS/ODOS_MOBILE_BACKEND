import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    delivery_instructions: Mapped[str | None] = mapped_column(String(280), nullable=True)
    delivery_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- Delivery lifecycle (see app/services/delivery_lifecycle_service.py) ---
    # A dedicated sub-state machine, independent of vendor_status: vendor_status
    # is the vendor's own fulfillment view (prep stages + dispatched/delivered)
    # and only ever moves forward one stage at a time, whereas delivery_status
    # can branch into rescheduled / customer_problem while the vendor's own
    # view stays parked at "out_for_delivery".
    # not_dispatched | out_for_delivery | rescheduled | customer_problem | delivered | failed
    delivery_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_dispatched", server_default="not_dispatched", index=True
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # customer | auto_release | admin_override
    confirmation_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delivery_problem_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivery_problem_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Scheduled the moment an order is dispatched (dispatched_at + grace window)
    # so the auto-release loop can do a simple indexed range scan instead of
    # re-deriving "how long has this been out for delivery" on every pass.
    auto_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the "confirm in ~12h or we auto-complete" reminder push has gone
    # out, so the auto-release loop doesn't re-notify on every pass.
    delivery_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # not_eligible | eligible | settled | held — denormalized for fast admin
    # querying; the source of truth for "was the vendor actually paid" remains
    # the VendorWalletTransaction row (see settle_vendor_wallets_for_order).
    settlement_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_eligible", server_default="not_eligible", index=True
    )
    reschedule_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reschedule_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    dispatch_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dispatch_photo_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    departure_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Courier fulfilment (opt-in; see app/models/courier.py) ---
    # Null for every order that stays on today's flow: vendor dispatches to
    # their own external rider, delivery_status moves straight to
    # out_for_delivery, unchanged. Set only for orders a courier claimed or
    # was assigned. The delivery_status state machine gains courier states
    # (ready_for_pickup, courier_assigned, picked_up, delivered_by_courier) as
    # a Phase 2 addition -- deliberately not wired here, so this column can
    # land and be tested without touching the transition table the existing,
    # working vendor-dispatch flow depends on.
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    courier_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    courier_picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The courier's own certification -- parallel to, and independent of, the
    # customer-confirm fields below. Moving delivery_status forward on this
    # alone would repeat the vendor self-certification problem this codebase
    # already rejected once; settlement still waits for customer confirmation
    # or auto-release, exactly as it does today.
    courier_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_proof_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivery_proof_note: Mapped[str | None] = mapped_column(String(280), nullable=True)

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
    promotion_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
    timeline: Mapped[list["OrderStatusEvent"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusEvent.occurred_at.asc()",
    )
    # One per vendor on the order. Single-vendor orders -- the overwhelming
    # majority -- have exactly one, and behave identically to before.
    packages: Mapped[list["OrderPackage"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderPackage.package_number.asc()",
    )


class OrderStatusEvent(Base):
    """Append-only audit trail powering the live delivery timeline shown to
    customer, vendor, and admin. Written once per status transition; never
    updated or deleted individually (rows are removed only via the order's
    cascade)."""

    __tablename__ = "order_status_events"

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
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Nullable: "system" events (auto-release) have no specific account behind
    # them. Populated wherever the caller has an authenticated user in hand.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Structured detail beyond the free-text note — e.g. {"reason": "...",
    # "previous_delivery_status": "...", "evidence_url": "..."}.
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    order: Mapped["Order"] = relationship(back_populates="timeline")


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
    is_flash_sale: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    flash_sale_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flash_sale_events.id", ondelete="SET NULL"),
        nullable=True,
    )
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

    # Custody of the goods. Money must not move until the item is back, so these
    # record who confirmed that and in what condition. A refund settled without
    # a return -- a damaged item the seller does not want back, say -- is still
    # possible, but has to be waived deliberately rather than by omission.
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    received_condition_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    return_waived: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
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

    order: Mapped["Order"] = relationship(back_populates="return_requests")
    timeline: Mapped[list["ReturnStatusEvent"]] = relationship(
        back_populates="return_request",
        cascade="all, delete-orphan",
        order_by="ReturnStatusEvent.occurred_at",
    )
    order_item: Mapped["OrderItem"] = relationship(back_populates="return_requests")
    user: Mapped["User"] = relationship(
        foreign_keys=[user_id],
        back_populates="return_requests",
    )
    reviewed_by_user: Mapped["User | None"] = relationship(
        foreign_keys=[reviewed_by_user_id],
        back_populates="reviewed_return_requests",
    )
    received_by_user: Mapped["User | None"] = relationship(foreign_keys=[received_by_user_id])


class ReturnStatusEvent(Base):
    """Append-only history of a return request.

    ReturnRequest carries only the *current* status, and reviewed_by/reviewed_at
    are overwritten on every change -- so without this table there is no way to
    answer who moved a request to under review, who approved it, and when. Money
    moves on these transitions, so the trail matters: customer, vendor and admin
    all read the same rows.

    Written once per transition; never updated or deleted individually."""

    __tablename__ = "return_status_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    return_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Nullable: a system transition (an expiry sweep) has no account behind it.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    return_request: Mapped["ReturnRequest"] = relationship(back_populates="timeline")


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
    vendor_reply: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    vendor_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class OrderPackage(Base):
    """One vendor's share of an order -- the bag that vendor actually carries.

    Why this exists:

    An order is one commercial event for the customer (one number, one receipt,
    one payment) but it can be several fulfilment events -- a dress from one
    shop, sneakers from another. Before this table those several events shared
    a single pair of columns on Order (`vendor_status`, `delivery_status`), and
    that produced three live faults on any multi-vendor order:

      * the forward-transition guard compared against the *shared* status, so
        the second vendor to touch the order was rejected with "orders move one
        stage at a time" and could not progress their own items at all;
      * the first vendor to tap "out for delivery" dispatched the whole order,
        told the customer everything was on the way, and started the
        auto-release clock for items still sitting on other vendors' shelves;
      * settlement ran with no vendor scope, so confirming receipt of the one
        package that did arrive paid *every* vendor on the order, including the
        ones that had shipped nothing.

    Each package owns its own fulfilment stage, its own delivery sub-state, its
    own auto-release clock, and its own settlement. Order.vendor_status and
    Order.delivery_status stay exactly where they are and keep being written --
    they become a derived roll-up of the packages (see
    `app.services.order_package_service.recompute_order_rollup`) so that every
    existing reader, query and index keeps working untouched.

    Items are not linked here by foreign key. Ownership is resolved by
    `vendor_user_id`, which is already how every other part of this codebase
    decides which items belong to which vendor; a second link would be a second
    source of truth free to drift from the first.

    `delivery_fee` is the money side of the same idea. The fee is charged per
    package and paid to the vendor that carries it, because under today's
    fulfilment model that vendor is the one paying a rider out of pocket. It
    carries no commission -- it is cost recovery, not revenue.
    """

    __tablename__ = "order_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable for the same reason OrderItem.vendor_user_id is: a vendor
    # account can be removed after the fact and the order must survive it.
    vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    store_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Snapshotted so a package still says which shop it came from after a
    # rename, exactly as Delivery snapshots its pickup address.
    store_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 1-based, stable, and what the customer sees as "Package 2 of 3".
    package_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # --- This vendor's own fulfilment stage ------------------------------
    # Same vocabulary as Order.vendor_status (pending | confirmed | processing
    # | ready | out_for_delivery | delivered | cancelled) and moved through the
    # same forward-transition table -- but per package, so one vendor being
    # fast can no longer lock another one out.
    vendor_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )
    # not_dispatched | out_for_delivery | rescheduled | customer_problem
    # | delivered | failed
    delivery_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_dispatched",
        server_default="not_dispatched",
        index=True,
    )

    # --- Money -----------------------------------------------------------
    #: Sum of this vendor's item line totals, before any discount.
    items_subtotal: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    #: This package's share of the order-level discount. Stored rather than
    #: re-derived so a voucher's allocation cannot drift after the fact.
    discount_share: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    #: What the customer paid to have *this* bag delivered, and what this
    #: vendor receives for delivering it. Zero when the package cleared the
    #: free-delivery threshold.
    delivery_fee: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    #: True when the fee is zero because the threshold was met, as opposed to
    #: the vendor pricing delivery at zero. Kept apart so the receipt can say
    #: "Free delivery (over GH₵299)" rather than a bare 0.00.
    delivery_fee_waived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # not_eligible | eligible | settled | held
    settlement_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_eligible",
        server_default="not_eligible",
        index=True,
    )

    # --- Delivery progress (per package, mirroring the old Order columns) --
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # customer | auto_release | admin_override
    confirmation_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Indexed range scan for the auto-release sweep, set at dispatch.
    auto_release_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    auto_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_problem_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivery_problem_reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reschedule_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reschedule_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    dispatch_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dispatch_photo_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    departure_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tracking_eta: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped["Order"] = relationship(back_populates="packages")

    @property
    def item_ids(self) -> list[uuid.UUID]:
        """Which of the order's items are in this bag.

        Derived rather than stored, because `order_items.vendor_user_id` is
        already the codebase's single answer to "whose item is this" and a
        second link would be free to drift from it. Serialized onto the API so
        a client can group items under packages without a second request.
        """
        order = self.order
        if order is None:
            return []
        return [
            item.id for item in order.items if item.vendor_user_id == self.vendor_user_id
        ]

    __table_args__ = (
        # One package per vendor per order. Two rows would reintroduce exactly
        # the ambiguity this table exists to remove.
        UniqueConstraint("order_id", "vendor_user_id", name="uq_order_package_order_vendor"),
    )
