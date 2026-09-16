from typing import Literal

from pydantic import BaseModel, Field

DeliveryMethodId = Literal["economy", "express", "same_day"]


class DeliveryQuoteCartItem(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=99)
    unit_price: float = Field(ge=0)


class DeliveryQuoteRequest(BaseModel):
    subtotal: float = Field(ge=0)
    region: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    selected_method: DeliveryMethodId = "economy"
    #: The cart itself. Delivery is priced per shop, so the only way to quote
    #: it honestly is to know which shops are involved — a flat `subtotal`
    #: cannot tell a GH₵300 basket from one shop apart from the same GH₵300
    #: spread across three. Omitted, the quote falls back to the old
    #: single-package behaviour, which keeps existing clients working.
    items: list[DeliveryQuoteCartItem] | None = None


class DeliveryPackageQuoteRead(BaseModel):
    """One shop's line in the delivery breakdown shown at checkout."""

    store_id: str | None = None
    store_name: str | None = None
    items_subtotal: float
    delivery_fee: float
    #: Free because the basket cleared this shop's threshold, rather than
    #: because the shop never charges. The two read differently to a customer.
    fee_waived: bool = False
    free_threshold: float = 0
    #: How much more with *this shop* would make its delivery free. Null when
    #: there is nothing left to gain.
    amount_to_free_delivery: float | None = None


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
    #: Per-shop breakdown of `shipping_amount`. Empty when the request didn't
    #: send its items. The checkout screen shows this so a customer paying
    #: three delivery fees can see exactly which three shops they are for —
    #: the fee is per package precisely because each one is a real, separate
    #: journey, and hiding that would make the total look arbitrary.
    packages: list[DeliveryPackageQuoteRead] = []
