import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatThreadEnsurePayload(BaseModel):
    product_id: str | None = Field(default=None, min_length=1, max_length=100)
    product_title: str | None = Field(default=None, min_length=1, max_length=255)
    product_image_url: str | None = Field(default=None, max_length=500)

    @field_validator("product_id", "product_title", "product_image_url", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned = value.strip()
        return cleaned or None


class SupportChatThreadEnsurePayload(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("subject", mode="before")
    @classmethod
    def strip_subject(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned = value.strip()
        return cleaned or None


class SupportChatStatusUpdate(BaseModel):
    status: str = Field(pattern="^(waiting_on_admin|waiting_on_customer|resolved)$")


class ChatMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body", mode="before")
    @classmethod
    def strip_body(cls, value: str) -> str:
        cleaned = value.strip()
        return cleaned


class ChatStoreSummaryRead(BaseModel):
    id: str
    title: str
    image_key: str | None = None
    image_url: str | None = None


class ChatProductSummaryRead(BaseModel):
    id: str | None = None
    title: str | None = None
    image_url: str | None = None


class ChatCounterpartRead(BaseModel):
    user_id: uuid.UUID
    name: str
    avatar_url: str | None = None
    role: str


class ChatThreadRead(BaseModel):
    id: uuid.UUID
    customer_user_id: uuid.UUID
    vendor_user_id: uuid.UUID
    thread_type: str = "store"
    store: ChatStoreSummaryRead
    counterpart: ChatCounterpartRead
    subject: str | None = None
    product: ChatProductSummaryRead | None = None
    support_status: str | None = None
    assigned_admin_user_id: uuid.UUID | None = None
    assigned_admin_name: str | None = None
    assigned_admin_at: datetime | None = None
    resolved_at: datetime | None = None
    last_message_text: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatMessageRead(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    sender_user_id: uuid.UUID
    recipient_user_id: uuid.UUID
    sender_role: str
    body: str
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
