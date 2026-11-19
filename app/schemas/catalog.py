import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    #: "Free delivery" or "Free over GH₵X" — computed from this store's own
    #: delivery pricing, falling back to the platform default. Null when there
    #: is nothing worth shouting about; a shop charging an ordinary fee gets no
    #: badge rather than a badge announcing that it charges money.
    delivery_badge: str | None = None
    #: What this shop charges for standard delivery, after platform fallback.
    #: Surfaced so a store page can say the number rather than only the badge.
    delivery_fee_from: float | None = None

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


class StoreSectionRead(BaseModel):
    """A shelf on a store page, with the products currently on it."""

    id: uuid.UUID
    title: str
    slug: str
    sort_order: int
    products: list[ProductRead]

    model_config = ConfigDict(from_attributes=True)


class VendorCampaignBriefRead(BaseModel):
    """A campaign as its vendor submission page needs it: identity plus participation rules."""

    id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    banner_image_url: str | None = None
    thumbnail_image_url: str | None = None
    theme_color: str | None = None
    status: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    submission_deadline: datetime | None = None
    #: Effective close: the explicit deadline, or the campaign end when there is none.
    submission_closes_at: datetime | None = None
    accepts_submissions: bool = False
    requires_proposed_price: bool = False
    min_discount_percent: int | None = None
    max_products: int | None = None
    product_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class VendorCampaignSubmissionRead(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    product_id: str
    product_title: str
    product_image_url: str | None = None
    #: The product's price today, for comparison against what was proposed.
    current_price: int | None = None
    proposed_price: int | None = None
    proposed_old_price: int | None = None
    discount_percent: int | None = None
    vendor_note: str | None = None
    status: str
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VendorCampaignWorkspaceRead(BaseModel):
    campaign: VendorCampaignBriefRead
    submissions: list[VendorCampaignSubmissionRead] = []


class VendorCampaignSubmissionItem(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    proposed_price: int | None = Field(default=None, ge=1)
    vendor_note: str | None = Field(default=None, max_length=255)


class VendorCampaignSubmissionPayload(BaseModel):
    items: list[VendorCampaignSubmissionItem] = Field(min_length=1, max_length=100)


class VendorCampaignSubmissionOutcome(BaseModel):
    product_id: str
    accepted: bool
    #: Machine-readable refusal: duplicate, not_yours, already_submitted, price_required,
    #: price_invalid, discount_too_small. Null when accepted.
    reason: str | None = None
    detail: str | None = None


class VendorCampaignSubmissionResult(BaseModel):
    campaign_slug: str
    submitted: int
    rejected: int
    outcomes: list[VendorCampaignSubmissionOutcome] = []


class CampaignParticipationRead(BaseModel):
    """Admin-facing participation counters for one campaign."""

    campaign_id: uuid.UUID
    campaign_slug: str
    vendors_notified: int = 0
    vendors_notified_at: datetime | None = None
    vendors_submitted: int = 0
    eligible_vendors: int = 0
    products_nominated: int = 0
    products_approved: int = 0
    products_rejected: int = 0
    products_pending: int = 0
