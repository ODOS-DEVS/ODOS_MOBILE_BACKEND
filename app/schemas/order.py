import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderItemCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=120)
    image_url: str | None = Field(default=None, max_length=500)
    image_key: str | None = Field(default=None, max_length=100)
    quantity: int = Field(ge=1, le=99)
    unit_price: float = Field(ge=0)
    selected_color: str | None = Field(default=None, max_length=60)
    selected_size: str | None = Field(default=None, max_length=60)

    @field_validator(
        "product_id",
        "title",
        "category",
        "image_url",
        "image_key",
        "selected_color",
        "selected_size",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned_value = value.strip()
        return cleaned_value or None


class OrderCreate(BaseModel):
    source: Literal["buy_now", "cart"] = "buy_now"
    items: list[OrderItemCreate] = Field(min_length=1)
    subtotal_amount: float = Field(ge=0)
    shipping_amount: float = Field(default=0, ge=0)
    delivery_method: Literal["economy", "express", "same_day"] = "economy"
    discount_amount: float = Field(default=0, ge=0)
    total_amount: float = Field(ge=0)
    voucher_code: str | None = Field(default=None, min_length=2, max_length=40)

    address_full_name: str = Field(min_length=1, max_length=120)
    address_phone: str = Field(min_length=1, max_length=30)
    address_street: str = Field(min_length=1, max_length=255)
    address_city: str = Field(min_length=1, max_length=120)
    address_region: str = Field(min_length=1, max_length=120)
    delivery_instructions: str | None = Field(default=None, max_length=280)

    payment_type: str = Field(min_length=1, max_length=30)
    payment_label: str = Field(min_length=1, max_length=120)
    payment_network: str | None = Field(default=None, max_length=60)
    payment_phone: str | None = Field(default=None, max_length=30)
    payment_last4: str | None = Field(default=None, min_length=4, max_length=4)

    @field_validator(
        "address_full_name",
        "address_phone",
        "address_street",
        "address_city",
        "address_region",
        "delivery_instructions",
        "payment_type",
        "payment_label",
        "payment_network",
        "payment_phone",
        "payment_last4",
        "voucher_code",
        mode="before",
    )
    @classmethod
    def strip_order_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned_value = value.strip()
        return cleaned_value or None


class OrderItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    category: str | None
    image_url: str | None
    image_key: str | None
    quantity: int
    unit_price: float
    line_total: float
    is_returnable: bool
    selected_color: str | None
    selected_size: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReturnRequestCreate(BaseModel):
    order_item_id: uuid.UUID
    request_type: Literal["refund", "exchange", "return"]
    quantity: int = Field(default=1, ge=1, le=99)
    reason: str = Field(min_length=2, max_length=160)
    details: str | None = Field(default=None, max_length=1000)
    evidence_image_urls: list[str] | None = None

    @field_validator("reason", "details", mode="before")
    @classmethod
    def strip_return_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned_value = value.strip()
        return cleaned_value or None


class ReturnStatusEventRead(BaseModel):
    id: uuid.UUID
    status: str
    actor_role: str
    note: str | None = None
    refund_amount: float | None = None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReturnRequestRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_item_id: uuid.UUID
    user_id: uuid.UUID
    request_type: str
    status: str
    quantity: int
    reason: str
    details: str | None
    evidence_image_urls: list[str] | None
    admin_note: str | None
    refund_amount: float | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    resolved_at: datetime | None
    collected_at: datetime | None = None
    received_at: datetime | None = None
    received_condition_note: str | None = None
    return_waived: bool = False
    created_at: datetime
    updated_at: datetime
    # The same rows for customer, vendor and admin, so a dispute has one
    # account of what happened rather than three.
    timeline: list[ReturnStatusEventRead] = []

    model_config = ConfigDict(from_attributes=True)


class OrderStatusEventRead(BaseModel):
    id: uuid.UUID
    status: str
    actor_role: str
    actor_id: uuid.UUID | None = None
    note: str | None
    event_metadata: dict | None = None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderDeliveryRatingUpdate(BaseModel):
    rating: int = Field(ge=1, le=5)


class OrderRescheduleRequest(BaseModel):
    note: str | None = Field(default=None, max_length=280)

    @field_validator("note", mode="before")
    @classmethod
    def strip_reschedule_note(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class OrderDeliveryProblemRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=40)
    details: str | None = Field(default=None, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("details", mode="before")
    @classmethod
    def strip_details(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class OrderRead(BaseModel):
    id: uuid.UUID
    order_number: str
    source: str
    status: str
    vendor_status: str
    payment_status: str
    payment_provider: str
    payment_reference: str | None
    subtotal_amount: float
    shipping_amount: float
    delivery_method: str
    total_amount: float
    progress: float | None
    tracking_eta: str | None
    cancellation_reason: str | None
    delivery_instructions: str | None
    delivery_rating: int | None
    delivery_rated_at: datetime | None
    delivery_status: str
    dispatched_at: datetime | None
    confirmation_method: str | None
    delivery_problem_reason: str | None
    delivery_problem_reported_at: datetime | None
    auto_release_at: datetime | None
    settlement_status: str
    reschedule_requested_at: datetime | None
    reschedule_note: str | None
    dispatch_photo_url: str | None
    departure_notified_at: datetime | None
    address_full_name: str
    address_phone: str
    address_street: str
    address_city: str
    address_region: str
    payment_type: str
    payment_label: str
    payment_network: str | None
    payment_phone: str | None
    payment_last4: str | None
    voucher_code: str | None
    voucher_title: str | None
    discount_amount: float
    placed_at: datetime
    paid_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    refunded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemRead]
    return_requests: list[ReturnRequestRead] = []
    timeline: list[OrderStatusEventRead] = []

    model_config = ConfigDict(from_attributes=True)
