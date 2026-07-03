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


class PromotionCalculateRequest(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1)
    shipping_amount: float = Field(default=0, ge=0)
    voucher_code: str | None = Field(default=None, max_length=40)
    include_auto_apply: bool = True

    @field_validator("voucher_code", mode="before")
    @classmethod
    def normalize_optional_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class AppliedPromotionRead(BaseModel):
    voucher_id: uuid.UUID
    code: str
    title: str
    promotion_type: str
    discount_type: str
    discount_amount: float
    priority: int
    stackable: bool


class RejectedPromotionRead(BaseModel):
    code: str | None = None
    voucher_id: uuid.UUID | None = None
    reason: str
    rejection_code: str


class PromotionCalculateRead(BaseModel):
    subtotal_amount: float
    shipping_amount: float
    discount_amount: float
    total_amount: float
    eligible_subtotal_amount: float
    applied_promotions: list[AppliedPromotionRead]
    rejected_promotions: list[RejectedPromotionRead]


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
