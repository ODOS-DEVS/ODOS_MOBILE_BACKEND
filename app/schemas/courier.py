"""Courier-facing request/response shapes."""

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
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    vendor_id: str | None
    vendor_name: str | None
    dropoff_address: str
    subtotal_amount: float
    status: str
    sla_deadline: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
