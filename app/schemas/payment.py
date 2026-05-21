import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.order import OrderCreate, OrderRead


class CheckoutSessionCreate(OrderCreate):
    callback_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)

    @field_validator("callback_url", "cancel_url", mode="before")
    @classmethod
    def strip_urls(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned_value = value.strip()
        return cleaned_value or None


class CheckoutSessionRead(BaseModel):
    order_id: uuid.UUID
    order_number: str
    reference: str
    authorization_url: str
    access_code: str
    amount: float
    currency: str
    payment_status: str


class PaymentVerificationRead(BaseModel):
    order: OrderRead
    reference: str
    payment_status: str
    provider_status: str
    paid_at: datetime | None = None
    verified_at: datetime | None = None
    message: str


class AdminFinanceOverviewRead(BaseModel):
    currency: str
    current_balance: float
    vendor_liability_balance: float
    commission_balance: float
    gross_collected_total: float
    processor_fee_total: float
    refunded_total: float
    total_payouts_sent: float
    pending_withdrawal_total: float
    approved_withdrawal_total: float
    paid_order_count: int
    paid_order_volume: float


class AdminPaymentTransactionRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    user_id: uuid.UUID
    customer_email: str
    provider: str
    reference: str
    amount: float
    currency: str
    status: str
    preferred_channel: str | None = None
    processor_fee_amount: float
    gateway_response: str | None = None
    provider_transaction_id: str | None = None
    paid_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminPlatformLedgerEntryRead(BaseModel):
    id: uuid.UUID
    kind: str
    direction: str
    title: str
    description: str | None = None
    amount: float
    current_balance_after: float
    vendor_liability_balance_after: float
    commission_balance_after: float
    order_id: uuid.UUID | None = None
    order_number: str | None = None
    payment_transaction_id: uuid.UUID | None = None
    payment_reference: str | None = None
    return_request_id: uuid.UUID | None = None
    vendor_withdrawal_request_id: uuid.UUID | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
