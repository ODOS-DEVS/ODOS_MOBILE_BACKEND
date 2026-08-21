from datetime import datetime
import uuid

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
    flash_sale_ends_at: datetime | None = None
    flash_sale_event_slug: str | None = None
    flash_sale_event_title: str | None = None
    flash_sale_stock_limit: int | None = None
    flash_sale_units_remaining: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FlashSaleEventRead(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None = None
    image_url: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime
    sort_order: int
    product_count: int = 0
    seconds_remaining: int = 0

    model_config = ConfigDict(from_attributes=True)


class MarketRead(BaseModel):
    id: str
    slug: str
    title: str
    image_key: str
    image_url: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class PromoBannerRead(BaseModel):
    id: uuid.UUID
    title: str
    subtitle: str | None = None
    cta_label: str
    cta_link: str | None = None
    image_url: str | None = None
    accent: str | None = None
    sort_order: int
    campaign_tag: str | None = None
    link_type: str | None = None
    placement: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MerchandisingCampaignRead(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    banner_image_url: str | None = None
    thumbnail_image_url: str | None = None
    icon_key: str | None = None
    theme_color: str | None = None
    is_featured: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    display_priority: int = 0
    product_count: int = 0
    seconds_remaining: int = 0

    model_config = ConfigDict(from_attributes=True)


class MerchandisingCampaignDetailRead(MerchandisingCampaignRead):
    products: list[ProductRead] = []
    has_more: bool = False


class DealsHubSectionRead(BaseModel):
    key: str
    title: str
    subtitle: str | None = None
    kind: str
    count: int | None = None
    badge: str | None = None
    slug: str | None = None


class DealsHubRead(BaseModel):
    banners: list[PromoBannerRead]
    flash_events: list[FlashSaleEventRead]
    promotions: list["StoreVoucherRead"]
    deal_products: list[ProductRead]
    campaigns: list[MerchandisingCampaignRead] = []
    sections: list[DealsHubSectionRead]
    campaign_tags: list[dict[str, str]]


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
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None
    instagram_url: str | None = None
    facebook_url: str | None = None
    tiktok_url: str | None = None
    twitter_url: str | None = None
    whatsapp_url: str | None = None
    website_url: str | None = None
    email: str | None
    city: str | None
    region: str | None
    distance_km: str | None
    travel_minutes: str | None
    description: str | None
    status: str
    sort_order: int
    is_on_vacation: bool = False
    vacation_message: str | None = None
    business_hours: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class SearchResultRead(BaseModel):
    """Search result with product details and relevance score."""

    product: ProductRead
    relevance_score: float
    reason: str


class SearchResponseRead(BaseModel):
    """Search response with results list."""

    query: str
    results: list[SearchResultRead]
    total_count: int
    has_more: bool


from app.schemas.voucher import StoreVoucherRead  # noqa: E402

DealsHubRead.model_rebuild()
