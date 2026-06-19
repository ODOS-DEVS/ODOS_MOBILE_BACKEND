from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


SUPPORTED_BEHAVIOR_EVENT_TYPES = frozenset(
    {
        "product_view",
        "product_click",
        "search_query",
        "search_result_click",
        "add_to_cart",
        "remove_from_cart",
        "add_to_wishlist",
        "remove_from_wishlist",
        "purchase",
    }
)


class BehaviorEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=40)
    product_id: str | None = Field(default=None, max_length=100)
    store_id: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=120)
    search_query: str | None = Field(default=None, max_length=255)
    source_screen: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None

    @field_validator(
        "event_type",
        "product_id",
        "store_id",
        "category",
        "search_query",
        "source_screen",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: str) -> str:
        return value.strip().lower()


class BehaviorEventBatchCreate(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    events: list[BehaviorEventCreate] = Field(min_length=1, max_length=50)

    @field_validator("session_id", mode="before")
    @classmethod
    def strip_session(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class BehaviorEventBatchRead(BaseModel):
    accepted: int
    session_id: str | None = None
