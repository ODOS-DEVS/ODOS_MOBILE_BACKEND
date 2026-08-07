import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AssistantMessageInput(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AssistantReferenceContext(BaseModel):
    """Entity the shopper opened the assistant from — a storefront, a specific
    product, or the checkout screen."""

    type: Literal["store", "product", "checkout"] = "store"
    store_id: str | None = Field(default=None, max_length=64)
    store_name: str | None = Field(default=None, max_length=160)
    market_title: str | None = Field(default=None, max_length=120)
    vendor_user_id: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=120)
    product_id: str | None = Field(default=None, max_length=100)
    product_title: str | None = Field(default=None, max_length=255)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[AssistantMessageInput] = Field(default_factory=list, max_length=12)
    screen: str | None = Field(default=None, max_length=80)
    conversation_id: uuid.UUID | None = None
    context: AssistantReferenceContext | None = None


class AssistantActionRead(BaseModel):
    label: str = Field(max_length=80)
    route: str = Field(max_length=160)
    params: dict[str, str] | None = None


class AssistantProductRead(BaseModel):
    id: str
    title: str
    price: int
    old_price: int | None = None
    discount: str | None = None
    image_url: str | None = None
    image_key: str | None = None
    store_id: str | None = None
    rating: float | None = None
    category: str | None = None


class AssistantStoreRead(BaseModel):
    id: str
    title: str
    category: str | None = None
    image_url: str | None = None
    image_key: str | None = None
    market_slug: str | None = None
    market_title: str | None = None
    rating: float | None = None


class AssistantMessageRead(BaseModel):
    id: str
    role: str
    content: str
    suggested_actions: list[AssistantActionRead] | None = None
    products: list[AssistantProductRead] | None = None
    stores: list[AssistantStoreRead] | None = None
    escalated_to_support: bool = False
    feedback_rating: int | None = None
    created_at: datetime | None = None


class AssistantNudgeRead(BaseModel):
    message: str = Field(max_length=500)
    prompt: str = Field(max_length=500)
    kind: str = Field(max_length=40)


class AssistantSessionResponse(BaseModel):
    conversation_id: str | None = None
    messages: list[AssistantMessageRead] = Field(default_factory=list)
    nudge: AssistantNudgeRead | None = None
    context: AssistantReferenceContext | None = None


class AssistantChatResponse(BaseModel):
    reply: str
    suggested_actions: list[AssistantActionRead] = Field(default_factory=list)
    escalated_to_support: bool = False
    conversation_id: str | None = None
    message_id: str | None = None
    products: list[AssistantProductRead] = Field(default_factory=list)
    stores: list[AssistantStoreRead] = Field(default_factory=list)


class AssistantFeedbackRequest(BaseModel):
    message_id: uuid.UUID
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=500)


class AssistantFeedbackResponse(BaseModel):
    message_id: str
    rating: int


class AssistantStatusResponse(BaseModel):
    enabled: bool
    provider: str
    model: str | None = None
    llm_reachable: bool | None = None
    llm_error: str | None = None
