import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.order import OrderItemCreate

VoucherScope = Literal["odos", "store", "category", "product"]
VoucherAvailability = Literal["auto", "claim", "assigned", "private"]
VoucherWalletStatus = Literal["active", "used", "expired"]


class VoucherPreviewRequest(BaseModel):
    voucher_code: str = Field(min_length=2, max_length=40)
    items: list[OrderItemCreate] = Field(min_length=1)
    shipping_amount: float = Field(default=0, ge=0)

    @field_validator("voucher_code", mode="before")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class VoucherSuggestionsRequest(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1)
    shipping_amount: float = Field(default=0, ge=0)


class VoucherPreviewRead(BaseModel):
    voucher_id: uuid.UUID
    code: str
    title: str
    issuer_name: str | None = None
    scope: VoucherScope
    availability: VoucherAvailability
    store_id: str | None = None
    store_name: str | None = None
    reward_text: str
    discount_amount: float
    eligible_subtotal_amount: float
    subtotal_amount: float
    shipping_amount: float
    total_amount: float


class VoucherWalletRead(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    issuer_name: str | None = None
    scope: VoucherScope
    availability: VoucherAvailability
    store_id: str | None = None
    store_name: str | None = None
    reward_text: str
    min_subtotal: float
    expires_at: datetime | None = None
    status: VoucherWalletStatus
    source: str | None = None


class StoreVoucherRead(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    issuer_name: str | None = None
    scope: VoucherScope
    availability: VoucherAvailability
    store_id: str | None = None
    store_name: str | None = None
    reward_text: str
    min_subtotal: float
    expires_at: datetime | None = None
    claimed: bool = False
    campaign_tag: str | None = None
    discount_type: str | None = None
    approval_status: str | None = None
