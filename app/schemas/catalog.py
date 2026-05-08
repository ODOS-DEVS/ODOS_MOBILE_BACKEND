from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CategoryRead(BaseModel):
    id: str
    slug: str
    title: str
    subtitle: str
    image_key: str
    image_url: str | None = None
    subcategories: list[str] | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class ProductRead(BaseModel):
    id: str
    audience_slug: str | None
    section: str | None
    placement_tags: list[str] | None = None
    title: str
    category: str | None
    subcategory: str | None = None
    category_slugs: list[str] | None = None
    subcategory_slugs: list[str] | None = None
    price: int
    old_price: int | None
    discount: str | None
    rating: float | None
    reviews: str | None
    image_key: str
    image_url: str | None
    image_urls: list[str] | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None
    description: str | None
    stock: int
    status: str
    store_id: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MarketRead(BaseModel):
    id: str
    slug: str
    title: str
    image_key: str
    image_url: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class StoreRead(BaseModel):
    id: str
    slug: str
    title: str
    category: str | None
    audience_slugs: list[str] | None = None
    market_id: str | None
    market_slug: str | None
    image_key: str
    image_url: str | None
    image_banner_key: str | None
    image_banner_url: str | None
    rating: float | None
    address: str | None
    phone: str | None
    email: str | None
    city: str | None
    region: str | None
    distance_km: str | None
    travel_minutes: str | None
    description: str | None
    status: str
    sort_order: int

    model_config = ConfigDict(from_attributes=True)
