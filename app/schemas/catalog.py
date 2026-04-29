from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CategoryRead(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str
    image_key: str
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class ProductRead(BaseModel):
    id: str
    audience_slug: str | None
    section: str | None
    title: str
    category: str | None
    price: int
    old_price: int | None
    discount: str | None
    rating: float | None
    reviews: str | None
    image_key: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MarketRead(BaseModel):
    id: str
    slug: str
    title: str
    image_key: str
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class StoreRead(BaseModel):
    id: str
    slug: str
    title: str
    category: str | None
    market_slug: str | None
    image_key: str
    image_banner_key: str | None
    rating: float | None
    address: str | None
    phone: str | None
    email: str | None
    city: str | None
    distance_km: str | None
    travel_minutes: str | None
    description: str | None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)
