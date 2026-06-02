import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import OrderCreate, OrderRead


class CustomerWalletTopUpCreate(BaseModel):
    amount: float = Field(gt=0)
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


class WalletCheckoutCreate(OrderCreate):
    pass


class WalletCheckoutRead(BaseModel):
    order: OrderRead
    wallet_balance_after: float
    message: str
