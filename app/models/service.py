"""Services marketplace domain.

Odos sells two different things through one marketplace. A product is bought
and delivered; a service is *performed*, by a person, usually at an agreed
time and often at a particular place. Those differ enough operationally that
forcing a booking through the product order pipeline would corrupt both, so
services get their own listing and booking tables.

What they deliberately do NOT get is their own account system, payment
gateway, notification channel or payout process. A provider is an existing
`Store` with services switched on (`Store.offerings`), money moves through the
existing `PaymentTransaction` + `PlatformLedgerEntry` + `VendorWallet`
infrastructure, and completion credits the same vendor wallet an order does.
See `app/services/service_booking_service.py`.
"""

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.catalog import Store
    from app.models.user import User


# --------------------------------------------------------------------------
# Vocabularies
#
# Kept as module constants rather than database enums: Postgres enum changes
# need a migration, and the marketplace is expected to grow new pricing and
# booking shapes. Validation happens in the service layer against these, and
# CheckConstraints below pin the values the booking engine actually branches
# on.
# --------------------------------------------------------------------------

#: How a listing's price is arrived at. Drives what the customer is shown and
#: which booking flows are legal for the service.
PRICING_MODELS = (
    "fixed",        # one price, bookable immediately
    "starting_at",  # indicative; final price confirmed by the provider
    "hourly",       # price x hours
    "package",      # customer picks one of several packages
    "quote",        # no price until the provider quotes
    "consultation", # free first conversation
)

#: How a booking comes into existence. A service may support exactly one.
BOOKING_TYPES = (
    "instant",       # pick a slot, pay, confirmed
    "request",       # pick a slot, provider accepts or declines
    "quote",         # describe the job, provider quotes, customer accepts
    "consultation",  # talk first, no slot or money up front
)

#: Where the work happens. A service may support several.
LOCATION_MODES = (
    "provider",  # at the provider's premises
    "customer",  # provider travels to the customer
    "remote",    # online
    "agreed",    # somewhere arranged between the two
)

#: When money is taken.
#:
#: There is no deposit option. Odos takes the full amount at booking and
#: holds it in escrow until the customer confirms the work, which gives the
#: customer the protection a deposit was meant to provide without leaving a
#: balance to chase, a provider part-paid for a finished job, or two payment
#: rows to reconcile per booking.
PAYMENT_TIMINGS = (
    "upfront",  # full amount, held by Odos until release
    "free",     # consultations and quote-first services
)

#: Booking lifecycle. Transitions are enforced in
#: `app/services/service_booking_state.py`; this is only the vocabulary.
BOOKING_STATUSES = (
    "pending_provider",  # request sent, awaiting accept/decline
    "pending_payment",   # accepted or instant, awaiting money
    "confirmed",
    "in_progress",
    "completed",
    "cancelled",
    "declined",
    "expired",
    "disputed",
)

#: Statuses that occupy the provider's calendar. The overlap constraint is
#: written against exactly this set, so a declined or expired booking frees
#: its slot immediately.
BLOCKING_BOOKING_STATUSES = (
    "pending_payment",
    "confirmed",
    "in_progress",
)


class ServiceCategory(Base):
    """Admin-managed taxonomy, one level of nesting via `parent_id`.

    A table rather than a constant so categories can be added from the admin
    dashboard without shipping code to four applications — the mobile app, the
    website and the admin all read this.
    """

    __tablename__ = "service_categories"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(60), nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("service_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    children: Mapped[list["ServiceCategory"]] = relationship(
        back_populates="parent", remote_side=lambda: [ServiceCategory.parent_id]
    )
    parent: Mapped["ServiceCategory | None"] = relationship(
        back_populates="children", remote_side=lambda: [ServiceCategory.id]
    )


class Service(Base):
    """One bookable offering belonging to a store."""

    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint(
            "pricing_model IN ('fixed','starting_at','hourly','package','quote','consultation')",
            name="ck_services_pricing_model",
        ),
        CheckConstraint(
            "booking_type IN ('instant','request','quote','consultation')",
            name="ck_services_booking_type",
        ),
        CheckConstraint(
            "payment_timing IN ('upfront','free')",
            name="ck_services_payment_timing",
        ),
        CheckConstraint("base_price >= 0", name="ck_services_base_price_non_negative"),
        CheckConstraint(
            "deposit_percent IS NULL OR (deposit_percent > 0 AND deposit_percent <= 100)",
            name="ck_services_deposit_percent_range",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_services_duration_positive",
        ),
        Index("ix_services_store_status", "store_id", "status"),
        Index("ix_services_category_status", "category_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Denormalised from the store so provider-scoped queries and permission
    #: checks do not need a join on every request, mirroring
    #: `OrderItem.vendor_user_id`.
    vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subcategory_id: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    pricing_model: Mapped[str] = mapped_column(String(20), nullable=False, default="fixed")
    booking_type: Mapped[str] = mapped_column(String(20), nullable=False, default="request")
    #: "upfront" is the escrow model: the customer pays in full and Odos holds it. The default
    #: was left at "on_completion" when escrow dropped deposits, which both this column's own
    #: check constraint and ServiceWrite's validator reject -- so any service created without an
    #: explicit payment_timing failed.
    payment_timing: Mapped[str] = mapped_column(String(20), nullable=False, default="upfront")

    #: Float with `round_money`, matching `Order.total_amount` and the ledger.
    #: Products store integer prices, but an hourly rate of 37.50 and a 30%
    #: deposit both need the decimal, and the financial layer this feeds is
    #: float throughout.
    base_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    #: Legacy. Deposits were removed when escrow landed; no permitted payment_timing uses it.
    deposit_percent: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: How long one appointment occupies the calendar. Null for services with
    #: no slot (quote and consultation flows).
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Cleanup/travel gap enforced after each appointment.
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    location_modes: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)), nullable=True)
    #: Free-text coverage for providers who travel, e.g. ["Accra", "Tema"].
    service_areas: Mapped[list[str] | None] = mapped_column(ARRAY(String(120)), nullable=True)

    #: Hours before the start time that booking closes.
    lead_time_hours: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    #: How far ahead the calendar is open.
    max_advance_days: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)

    requirements_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: draft | pending_review | active | paused | rejected | archived.
    #: Only 'active' is publicly bookable; enforced in the booking service.
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", server_default="draft", index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    booking_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    store: Mapped["Store"] = relationship()
    images: Mapped[list["ServiceImage"]] = relationship(
        back_populates="service", cascade="all, delete-orphan", order_by="ServiceImage.sort_order"
    )
    packages: Mapped[list["ServicePackage"]] = relationship(
        back_populates="service", cascade="all, delete-orphan", order_by="ServicePackage.sort_order"
    )
    addons: Mapped[list["ServiceAddon"]] = relationship(
        back_populates="service", cascade="all, delete-orphan", order_by="ServiceAddon.sort_order"
    )
    availability_rules: Mapped[list["ServiceAvailabilityRule"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )


class ServiceImage(Base):
    """Gallery and portfolio frames for a listing."""

    __tablename__ = "service_images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Portfolio frames are past work rather than what is being sold, so the
    #: listing gallery and the portfolio strip can be rendered separately.
    is_portfolio: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    service: Mapped["Service"] = relationship(back_populates="images")


class ServicePackage(Base):
    """A named tier of a service — Basic / Standard / Premium and the like."""

    __tablename__ = "service_packages"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_service_packages_price_non_negative"),
        UniqueConstraint("service_id", "slug", name="uq_service_packages_service_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Plain list of what the buyer gets, e.g. ["80 edited photos", "2 hours"].
    #: Deliberately unstructured: every trade describes its deliverables
    #: differently, and a fixed column set would fit photography and fail
    #: plumbing.
    inclusions: Mapped[list[str] | None] = mapped_column(ARRAY(String(200)), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    service: Mapped["Service"] = relationship(back_populates="packages")


class ServiceAddon(Base):
    """An optional extra a customer can attach to a booking."""

    __tablename__ = "service_addons"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_service_addons_price_non_negative"),
        CheckConstraint("max_quantity >= 1", name="ck_service_addons_max_quantity"),
        UniqueConstraint("service_id", "slug", name="uq_service_addons_service_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Extras that lengthen the appointment push the end time out, which the
    #: overlap check then honours — an extra hour of shooting must not let the
    #: next customer book over it.
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    service: Mapped["Service"] = relationship(back_populates="addons")


class ServiceAvailabilityRule(Base):
    """A recurring weekly window during which a service can be booked."""

    __tablename__ = "service_availability_rules"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_availability_weekday_range"),
        CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
        Index("ix_availability_service_weekday", "service_id", "weekday"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 0 = Monday, matching `datetime.weekday()`.
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    service: Mapped["Service"] = relationship(back_populates="availability_rules")


class ServiceAvailabilityException(Base):
    """A specific date that overrides the weekly rules.

    Scoped to the store rather than one service: a provider closing for a
    public holiday means every service they run is shut that day, and making
    them repeat the date per listing invites the one they forget.
    """

    __tablename__ = "service_availability_exceptions"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "service_id", "exception_date", name="uq_availability_exception_day"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Null applies the exception to every service the store runs.
    service_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=True, index=True
    )
    exception_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: False closes the day outright; True opens it with the times below,
    #: which is how a provider offers a one-off Sunday.
    is_available: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Booking(Base):
    """A customer's engagement of a service.

    Deliberately not an `Order`. An order is a basket of goods that ships;
    a booking is one provider's time, which can be requested before it is
    priced, quoted before it is agreed, and paid in two parts. Reusing
    `orders` would have meant nullable delivery columns, a second meaning for
    order status, and a 1:1 payment row that cannot express a deposit.

    Money still flows through the existing rails: `PaymentTransaction` rows
    point here via `booking_id`, and completion credits the same
    `VendorWallet` an order does.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_provider','pending_payment','confirmed','in_progress',"
            "'completed','cancelled','declined','expired','disputed')",
            name="ck_bookings_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_bookings_total_non_negative"),
        CheckConstraint("amount_paid >= 0", name="ck_bookings_paid_non_negative"),
        CheckConstraint(
            "scheduled_end_at IS NULL OR scheduled_start_at IS NULL "
            "OR scheduled_end_at > scheduled_start_at",
            name="ck_bookings_schedule_order",
        ),
        Index("ix_bookings_customer_status", "customer_user_id", "status"),
        Index("ix_bookings_store_status", "store_id", "status"),
        Index("ix_bookings_service_start", "service_id", "scheduled_start_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Human reference, same shape of idea as `Order.order_number`.
    booking_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)

    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: RESTRICT, not CASCADE: a completed booking is a financial record. A
    #: listing being deleted must not take the money trail with it.
    provider_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_packages.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending_provider", index=True
    )
    booking_type: Mapped[str] = mapped_column(String(20), nullable=False)

    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Start + duration + add-on time + buffer. Stored rather than derived so
    #: the overlap constraint has a column to range over, and so a later
    #: change to the listing's duration cannot retroactively move a booking
    #: that was already agreed.
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_name: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Africa/Accra", server_default="Africa/Accra"
    )

    location_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location_region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    #: Every figure below is computed server-side from the listing at the
    #: moment of booking. Nothing here is accepted from the client.
    base_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    addons_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deposit_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    amount_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    #: Snapshot of the platform cut at agreement time. Keeping it on the row
    #: means a later change to the global rate cannot silently restate what a
    #: provider was promised for work already done.
    commission_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    commission_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    provider_net_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    #: unpaid | deposit_paid | paid | refunded | partially_refunded
    payment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unpaid", server_default="unpaid", index=True
    )

    #: Line-item breakdown of chosen add-ons, frozen at booking time.
    pricing_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    #: Holds a slot while the customer completes payment. Past this the
    #: sweeper expires the booking and the slot returns to the calendar —
    #: opening the booking screen must not reserve a slot forever.
    payment_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Escrow -------------------------------------------------------
    # Odos holds the money from payment until the work is confirmed. The
    # provider marking a booking complete is a claim, not a release; the
    # customer confirming it, or the grace window elapsing, is what pays.
    # Mirrors how `OrderPackage.auto_release_at` settles a delivery.
    #
    #: Provider says the work is done. Starts the grace window.
    work_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Customer agrees it is done. Releases immediately.
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the hold lapses on its own if the customer never responds.
    #: Without this a silent customer would strand a provider's money.
    auto_release_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the provider's wallet is credited, so a retried release,
    #: a replayed webhook or the sweeper racing a confirmation cannot pay twice.
    earnings_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: customer_confirmed | auto_released | admin_released -- why it paid out.
    earnings_release_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    service: Mapped["Service"] = relationship()
    store: Mapped["Store"] = relationship()
    customer: Mapped["User"] = relationship(foreign_keys=[customer_user_id])
    addons: Mapped[list["BookingAddon"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    status_history: Mapped[list["BookingStatusEvent"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="BookingStatusEvent.created_at",
    )


class BookingAddon(Base):
    """An add-on as purchased, priced at the time of booking."""

    __tablename__ = "booking_addons"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_booking_addons_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_booking_addons_price"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: SET NULL, with the title copied below: deleting an add-on from a
    #: listing must not rewrite what a past customer was charged for.
    addon_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_addons.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    line_total: Mapped[float] = mapped_column(Float, nullable=False)

    booking: Mapped["Booking"] = relationship(back_populates="addons")


class BookingStatusEvent(Base):
    """Audit trail of lifecycle changes, mirroring `OrderStatusEvent`."""

    __tablename__ = "booking_status_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    #: customer | provider | admin | system — who caused it, which matters in
    #: a dispute and decides which cancellation policy applies.
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    booking: Mapped["Booking"] = relationship(back_populates="status_history")


class ServiceQuotation(Base):
    """A provider's priced answer to a described job."""

    __tablename__ = "service_quotations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','sent','accepted','declined','expired','withdrawn')",
            name="ck_quotations_status",
        ),
        CheckConstraint(
            "quoted_amount IS NULL OR quoted_amount >= 0", name="ck_quotations_amount"
        ),
        Index("ix_quotations_store_status", "store_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="requested", server_default="requested", index=True
    )

    customer_brief: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(120), nullable=True)

    quoted_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="GHS", server_default="GHS")
    provider_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A quote is an offer with a shelf life; past this it cannot be accepted.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Set once the customer accepts and a booking is raised from the quote.
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ServiceReview(Base):
    """A review tied to one completed booking.

    The booking FK is unique, which is the whole anti-gaming mechanism: a
    review cannot exist without a booking, and a booking can carry only one.
    Eligibility (completed, and the reviewer is the customer) is checked in
    the service layer; uniqueness is guaranteed here where it cannot be raced.
    """

    __tablename__ = "service_reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_service_reviews_rating"),
        UniqueConstraint("booking_id", name="uq_service_reviews_booking"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: published | pending | hidden — moderation, mirroring product reviews.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="published", server_default="published", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
