from typing import Literal

from pydantic import BaseModel, Field

DeliveryMethodId = Literal["economy", "express", "same_day"]


class DeliveryQuoteRequest(BaseModel):
    subtotal: float = Field(ge=0)
    region: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    selected_method: DeliveryMethodId = "economy"


class DeliveryOptionRead(BaseModel):
    id: DeliveryMethodId
    title: str
    subtitle: str
    eta: str
    amount: float
    badge: str | None = None
    available: bool
    unavailable_reason: str | None = None


class DeliveryQuoteRead(BaseModel):
    options: list[DeliveryOptionRead]
    selected_method: DeliveryMethodId
    shipping_amount: float
    free_shipping_threshold: float
    same_day_cutoff_passed: bool
