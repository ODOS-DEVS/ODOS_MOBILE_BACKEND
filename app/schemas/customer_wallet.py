import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import OrderCreate, OrderRead


class CustomerWalletTopUpCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_type: Literal["card", "momo"] | None = None
    payment_label: str | None = Field(default=None, max_length=120)
    payment_network: str | None = Field(default=None, max_length=60)
    payment_phone: str | None = Field(default=None, max_length=30)
    payment_last4: str | None = Field(default=None, min_length=4, max_length=4)
    callback_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)


class CustomerWalletTopUpSessionRead(BaseModel):
    reference: str
    authorization_url: str
    access_code: str
    amount: float
    currency: str
    status: str


class CustomerWalletTransactionRead(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    amount: float
    balance_after: float
    order_id: uuid.UUID | None = None
    topup_id: uuid.UUID | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CustomerWalletRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    currency: str
    available_balance: float
    lifetime_topups: float
    lifetime_spend: float
    lifetime_refunds: float
    recent_transactions: list[CustomerWalletTransactionRead]

    model_config = ConfigDict(from_attributes=True)


class CustomerWalletTopUpVerificationRead(BaseModel):
    reference: str
    status: str
    message: str
    amount: float
    currency: str
    payment_label: str | None = None
    payment_type: str | None = None
    wallet: CustomerWalletRead


class WalletCheckoutCreate(OrderCreate):
    pass


class WalletCheckoutRead(BaseModel):
    order: OrderRead
    wallet_balance_after: float
    message: str
