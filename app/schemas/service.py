"""Request and response shapes for the services marketplace.

The booking request shape is the security boundary: it accepts *choices*
only. There is no price, total, commission or duration field anywhere in an
inbound model, so a client cannot propose what it should be charged even by
accident. Amounts appear in responses, computed by `service_pricing`.
"""

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.service import (
    BOOKING_TYPES,
    LOCATION_MODES,
    PAYMENT_TIMINGS,
    PRICING_MODELS,
)


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

class ServiceCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    icon: str | None = None
    image_url: str | None = None
    parent_id: str | None = None
    sort_order: int = 0
    #: Writable through ServiceCategoryWrite but, until now, never returned -- so the admin
    #: dashboard listed deactivated categories as active and `DELETE` (which deactivates rather
    #: than removing) appeared to do nothing. Additive and optional, so existing clients that
    #: ignore it are unaffected.
    is_active: bool = True


class ServiceCategoryWrite(BaseModel):
    slug: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=120)
    subtitle: str | None = Field(default=None, max_length=160)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=60)
    image_key: str | None = Field(default=None, max_length=100)
    image_url: str | None = Field(default=None, max_length=500)
    parent_id: str | None = None
    sort_order: int = 0
    is_active: bool = True

    @field_validator("slug")
    @classmethod
    def normalise_slug(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "-")


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------

class ServicePackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    description: str | None = None
    price: float
    duration_minutes: int | None = None
    inclusions: list[str] | None = None
    sort_order: int = 0


class ServiceAddonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    description: str | None = None
    price: float
    duration_minutes: int = 0
    max_quantity: int = 1
    sort_order: int = 0


class ServiceImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: str | None = None
    caption: str | None = None
    is_portfolio: bool = False
    sort_order: int = 0


class ServiceSummaryRead(BaseModel):
    """The card shape used by discovery lists."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    title: str
    summary: str | None = None
    store_id: str
    category_id: str | None = None
    pricing_model: str
    booking_type: str
    base_price: float
    currency: str
    duration_minutes: int | None = None
    location_modes: list[str] | None = None
    rating: float | None = None
    review_count: int = 0
    image_url: str | None = None


class ServiceDetailRead(ServiceSummaryRead):
    description: str | None = None
    payment_timing: str
    service_areas: list[str] | None = None
    lead_time_hours: int
    max_advance_days: int
    requirements_note: str | None = None
    cancellation_policy: str | None = None
    booking_count: int = 0
    packages: list[ServicePackageRead] = []
    addons: list[ServiceAddonRead] = []
    images: list[ServiceImageRead] = []


class ServiceWrite(BaseModel):
    """Provider-supplied listing fields.

    `status` is absent on purpose: publication goes through the controller's
    submit/approve path, so a provider cannot self-approve a listing into the
    public catalogue by setting a field.
    """

    title: str = Field(min_length=3, max_length=255)
    summary: str | None = Field(default=None, max_length=300)
    description: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    pricing_model: str = "fixed"
    booking_type: str = "request"
    payment_timing: str = "on_completion"
    base_price: float = Field(default=0.0, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    location_modes: list[str] | None = None
    service_areas: list[str] | None = None
    lead_time_hours: int = Field(default=2, ge=0, le=720)
    max_advance_days: int = Field(default=60, ge=1, le=365)
    requirements_note: str | None = None
    cancellation_policy: str | None = None

    @field_validator("pricing_model")
    @classmethod
    def known_pricing_model(cls, value: str) -> str:
        if value not in PRICING_MODELS:
            raise ValueError(f"Pricing model must be one of {', '.join(PRICING_MODELS)}.")
        return value

    @field_validator("booking_type")
    @classmethod
    def known_booking_type(cls, value: str) -> str:
        if value not in BOOKING_TYPES:
            raise ValueError(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
        return value

    @field_validator("payment_timing")
    @classmethod
    def known_payment_timing(cls, value: str) -> str:
        if value not in PAYMENT_TIMINGS:
            raise ValueError(f"Payment timing must be one of {', '.join(PAYMENT_TIMINGS)}.")
        return value

    @field_validator("location_modes")
    @classmethod
    def known_location_modes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - set(LOCATION_MODES))
        if unknown:
            raise ValueError(f"Unknown location option: {', '.join(unknown)}.")
        return value


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

class AvailabilityRuleWrite(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, value: time, info) -> time:
        start = info.data.get("start_time")
        if start and value <= start:
            raise ValueError("Closing time must be after opening time.")
        return value


class AvailabilityRuleRead(AvailabilityRuleWrite):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool = True


class AvailabilityExceptionWrite(BaseModel):
    exception_date: date
    is_available: bool = False
    start_time: time | None = None
    end_time: time | None = None
    note: str | None = Field(default=None, max_length=255)
    #: Null closes the date for every service the store runs.
    service_id: str | None = None


class SlotRead(BaseModel):
    start_at: datetime
    end_at: datetime


# --------------------------------------------------------------------------
# Bookings
# --------------------------------------------------------------------------

class AddonSelectionWrite(BaseModel):
    """A chosen extra. Price comes from the database, never from here."""

    addon_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=50)


class BookingQuoteRequest(BaseModel):
    """Ask what a configuration would cost, without booking it."""

    package_id: uuid.UUID | None = None
    addons: list[AddonSelectionWrite] = []
    hours: float | None = Field(default=None, gt=0, le=24)


class BookingCreate(BookingQuoteRequest):
    start_at: datetime | None = None
    location_mode: str | None = None
    location_address: str | None = Field(default=None, max_length=500)
    location_city: str | None = Field(default=None, max_length=120)
    customer_note: str | None = Field(default=None, max_length=2000)

    @field_validator("location_mode")
    @classmethod
    def known_location_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in LOCATION_MODES:
            raise ValueError("Unknown location option.")
        return value


class PricedAddonRead(BaseModel):
    title: str
    unit_price: float
    quantity: int
    line_total: float


class BookingQuoteRead(BaseModel):
    """What the backend says a configuration costs."""

    currency: str
    base_amount: float
    addons_amount: float
    total_amount: float
    deposit_amount: float
    due_now: float
    duration_minutes: int
    addons: list[PricedAddonRead] = []


class BookingAddonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    unit_price: float
    quantity: int
    line_total: float


class BookingStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None = None
    to_status: str
    actor_role: str
    note: str | None = None
    created_at: datetime


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_number: str
    service_id: str
    store_id: str
    status: str
    booking_type: str
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    location_mode: str | None = None
    location_address: str | None = None
    location_city: str | None = None
    customer_note: str | None = None
    currency: str
    base_amount: float
    addons_amount: float
    total_amount: float
    deposit_amount: float
    amount_paid: float
    payment_status: str
    payment_deadline_at: datetime | None = None
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    addons: list[BookingAddonRead] = []


class BookingDetailRead(BookingRead):
    #: Provider's cut, shown only to the provider and admins; the controller
    #: omits it for customers, for whom it is neither useful nor their business.
    provider_net_amount: float | None = None
    commission_amount: float | None = None
    status_history: list[BookingStatusEventRead] = []


class BookingTransitionRequest(BaseModel):
    """Ask to move a booking. The state machine decides if it may."""

    to_status: str
    note: str | None = Field(default=None, max_length=500)


class ServiceReviewWrite(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ServiceReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rating: int
    comment: str | None = None
    provider_response: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------
# Quotations
# --------------------------------------------------------------------------

class QuotationRequestWrite(BaseModel):
    """A customer describing a job. No price — that is what they are asking."""

    brief: str = Field(min_length=10, max_length=4000)
    preferred_date: date | None = None
    location_mode: str | None = None
    location_city: str | None = Field(default=None, max_length=120)

    @field_validator("location_mode")
    @classmethod
    def known_location_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in LOCATION_MODES:
            raise ValueError("Unknown location option.")
        return value


class QuotationSendWrite(BaseModel):
    """The provider's answer. The one price a human legitimately sets."""

    amount: float = Field(ge=0)
    message: str | None = Field(default=None, max_length=2000)
    estimated_duration_minutes: int | None = Field(default=None, gt=0)
    validity_days: int | None = Field(default=None, ge=1, le=90)


class QuotationAcceptWrite(BaseModel):
    start_at: datetime | None = None
    location_address: str | None = Field(default=None, max_length=500)
    customer_note: str | None = Field(default=None, max_length=2000)


class QuotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    service_id: str
    store_id: str
    status: str
    customer_brief: str
    preferred_date: date | None = None
    location_mode: str | None = None
    location_city: str | None = None
    quoted_amount: float | None = None
    currency: str
    provider_message: str | None = None
    estimated_duration_minutes: int | None = None
    expires_at: datetime | None = None
    booking_id: uuid.UUID | None = None
    created_at: datetime
