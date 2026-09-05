"""Courier-facing request/response shapes.

Two read models for a delivery on purpose:

`DeliveryOfferRead` is what an online rider sees for work they have *not* taken.
It carries the drop-off **area** and no customer identity, no phone, no street
address and no order value. A rider deciding whether to take a job needs to know
where it goes and how long they have; what the customer spent and where exactly
they live is not part of that decision, and handing it to every rider in the
pool is a data leak with no upside.

`DeliveryRead` is what the one rider holding the delivery sees, and it adds the
full address and coordinates. The split is enforced by which serializer the
endpoint uses, not by what the client chooses to render.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CourierProfileCreate(BaseModel):
    vehicle_type: str = Field(min_length=1, max_length=20)
    plate_number: str | None = Field(default=None, max_length=30)


class CourierProfileRead(BaseModel):
    id: uuid.UUID
    vendor_id: str | None
    vehicle_type: str
    plate_number: str | None
    is_online: bool
    rating: float | None
    total_deliveries: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CourierStatusUpdate(BaseModel):
    is_online: bool


class CourierLocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DeliveryOfferRead(BaseModel):
    """Pre-claim. Deliberately thin."""

    id: uuid.UUID
    delivery_id: uuid.UUID | None
    order_number: str
    vendor_id: str | None
    pickup_name: str | None
    pickup_area: str | None
    dropoff_area: str | None
    item_count: int
    status: str
    sla_deadline: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliveryRead(BaseModel):
    """Post-claim, and only for the rider who holds it."""

    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    status: str
    vendor_id: str | None
    pickup_name: str | None
    pickup_address: str | None
    pickup_latitude: float | None
    pickup_longitude: float | None
    dropoff_area: str | None
    dropoff_address: str | None
    dropoff_latitude: float | None
    dropoff_longitude: float | None
    customer_name: str | None
    delivery_instructions: str | None
    item_count: int
    attempt_count: int
    failure_reason: str | None
    offered_at: datetime | None
    accepted_at: datetime | None
    arrived_at_pickup_at: datetime | None
    picked_up_at: datetime | None
    arrived_at_customer_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliveryEventRead(BaseModel):
    event: str
    from_status: str | None
    to_status: str | None
    actor_type: str
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliveryTimelineRead(BaseModel):
    delivery: DeliveryRead
    events: list[DeliveryEventRead]


class DeliveryIntentRequest(BaseModel):
    """Body for the transition endpoints. `reason` is only read by the intents
    that record one (failures, customer-unavailable, release)."""

    reason: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=500)
