"""Wire shapes for price negotiation.

`min_acceptable_price` appears in exactly one schema here — the vendor's own
product settings — and in no shopper-facing or public model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NegotiationOfferRead(BaseModel):
    id: uuid.UUID
    actor_role: str
    created_by_user_id: uuid.UUID
    unit_price: int
    quantity: int
    message: str | None = None
    status: str
    created_at: datetime
    responded_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class NegotiationAgreementRead(BaseModel):
    id: uuid.UUID
    reference: str
    negotiation_id: uuid.UUID
    product_id: str
    listed_unit_price: int
    agreed_unit_price: int
    quantity: int
    currency: str
    status: str
    accepted_at: datetime
    expires_at: datetime
    #: Seconds until the deal lapses, counted by the server. Preferred over the
    #: browser comparing `expires_at` to its own clock, which is wrong often
    #: enough to matter on a countdown the shopper is racing.
    seconds_remaining: int = 0
    order_id: str | None = None
    order_number: str | None = None
    used_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class NegotiationRead(BaseModel):
    id: uuid.UUID
    reference: str
    product_id: str
    product_title: str
    product_image_url: str | None = None
    store_id: str
    store_name: str | None = None
    vendor_user_id: uuid.UUID
    customer_user_id: uuid.UUID
    customer_name: str | None = None
    thread_id: uuid.UUID | None = None
    selected_color: str | None = None
    selected_size: str | None = None
    listed_unit_price: int
    #: Price the product is listed at *now*, which may differ from the price
    #: when haggling opened.
    current_listed_price: int | None = None
    status: str
    #: Whose move it is: "customer", "vendor", or null when nothing is pending.
    awaiting: str | None = None
    latest_offer: NegotiationOfferRead | None = None
    agreement: NegotiationAgreementRead | None = None
    offers: list[NegotiationOfferRead] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NegotiationStartPayload(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    unit_price: int = Field(ge=1)
    quantity: int = Field(default=1, ge=1, le=99)
    message: str | None = Field(default=None, max_length=500)
    selected_color: str | None = Field(default=None, max_length=60)
    selected_size: str | None = Field(default=None, max_length=60)


class NegotiationOfferPayload(BaseModel):
    """A counter, from either side."""

    unit_price: int = Field(ge=1)
    quantity: int | None = Field(default=None, ge=1, le=99)
    message: str | None = Field(default=None, max_length=500)


class NegotiationRespondPayload(BaseModel):
    message: str | None = Field(default=None, max_length=500)


class VendorNegotiationSettings(BaseModel):
    """The vendor's own view of a product's negotiation configuration."""

    is_negotiable: bool = False
    #: Private. Returned only on this vendor-authenticated schema.
    min_acceptable_price: int | None = Field(default=None, ge=1)
    negotiation_max_quantity: int | None = Field(default=None, ge=1, le=99)
    negotiation_note: str | None = Field(default=None, max_length=255)


class AdminNegotiationRead(BaseModel):
    """A negotiation as the admin queue lists it.

    Carries both parties' display names because that is what an admin searches
    by, and no contact detail beyond them.
    """

    id: uuid.UUID
    reference: str
    status: str
    product_id: str
    product_title: str
    product_image_url: str | None = None
    store_id: str
    store_name: str | None = None
    vendor_user_id: uuid.UUID
    vendor_name: str | None = None
    customer_user_id: uuid.UUID
    customer_name: str | None = None
    listed_unit_price: int
    latest_offer_price: int | None = None
    agreed_unit_price: int | None = None
    quantity: int | None = None
    agreement_reference: str | None = None
    agreement_status: str | None = None
    order_number: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminNegotiationDetailRead(AdminNegotiationRead):
    """The dispute view: every offer in sequence, with the agreement it produced."""

    current_listed_price: int | None = None
    product_is_negotiable: bool = False
    selected_color: str | None = None
    selected_size: str | None = None
    offers: list[NegotiationOfferRead] = []
    agreement: NegotiationAgreementRead | None = None


class AdminNegotiationStatsRead(BaseModel):
    total: int = 0
    open: int = 0
    agreed: int = 0
    completed: int = 0
    rejected: int = 0
    expired: int = 0
    agreements_live: int = 0
    agreements_used: int = 0
    agreements_expired: int = 0
    negotiable_products: int = 0
    #: The deepest discount anyone actually completed a purchase at — the fastest
    #: way to spot a vendor being gamed, or gaming.
    deepest_discount_reference: str | None = None
    deepest_discount_percent: int | None = None


class AdminNegotiationToggle(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=255)


class NegotiationCountsRead(BaseModel):
    total: int = 0
    awaiting_me: int = 0
    awaiting_them: int = 0
    agreed: int = 0
    completed: int = 0
    rejected: int = 0
    expired: int = 0
