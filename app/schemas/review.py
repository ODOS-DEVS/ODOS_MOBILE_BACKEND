import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewUpsert(BaseModel):
    order_id: uuid.UUID
    product_id: str = Field(min_length=1, max_length=100)
    rating: float = Field(ge=0.5, le=5)
    comment: str = Field(min_length=8, max_length=500)

    @field_validator("product_id", "comment", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        return cleaned

    @field_validator("rating", mode="before")
    @classmethod
    def normalize_rating(cls, value: float | int | str) -> float:
        numeric = float(value)
        rounded = round(numeric * 2) / 2
        if numeric < 0.5 or numeric > 5 or abs(numeric - rounded) > 1e-6:
            raise ValueError("Rating must be between 0.5 and 5 in half-star steps.")
        return rounded


class ProductReviewRead(BaseModel):
    id: uuid.UUID
    product_id: str
    rating: float
    comment: str
    user_display_name: str
    created_at: datetime
    updated_at: datetime


class UserReviewRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    product_id: str
    title: str
    category: str | None = None
    image_key: str | None = None
    image_url: str | None = None
    rating: float
    comment: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
