import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models import VendorStatus


class AdminDashboardStatsRead(BaseModel):
    total_users: int
    total_vendors: int
    pending_vendor_applications: int
    total_stores: int
    total_products: int
    total_orders: int
    pending_orders: int
    total_revenue: float


class AdminUserRead(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone_number: str | None
    avatar_url: str | None
    roles: list[str]
    vendor_status: VendorStatus
    account_status: str
    joined_at: datetime


class AdminUserStatusUpdate(BaseModel):
    account_status: str = Field(min_length=1, max_length=30)

    @field_validator("account_status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminVendorRead(BaseModel):
    id: str
    user_id: uuid.UUID
    business_name: str
    business_category: str
    status: str
    email: str
    phone_number: str | None
    total_stores: int
    total_products: int
    total_orders: int
    total_sales: float
    joined_at: datetime


class AdminVendorStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminStoreRead(BaseModel):
    id: str
    vendor_id: str | None
    name: str
    slug: str
    description: str
    category: str
    audience_slugs: list[str] | None = None
    market_id: str | None
    location: str | None
    region: str
    city: str
    banner_image: str | None
    logo_image: str | None
    status: str
    created_at: datetime


class AdminStoreStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminMarketRead(BaseModel):
    id: str
    name: str
    slug: str
    image: str | None
    image_url: str | None = None
    status: str
    created_at: datetime


class AdminMarketUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=50)
    image: str | None = Field(default=None, max_length=200)
    status: str = Field(default="active", min_length=1, max_length=30)

    @field_validator("name", "slug", "image", "status", mode="before")
    @classmethod
    def strip_market_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AdminCategoryRead(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    image: str | None
    image_url: str | None = None
    subcategories: list[str] | None = None
    status: str
    created_at: datetime


class AdminCategoryUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=50)
    description: str = Field(default="", max_length=160)
    image: str | None = Field(default=None, max_length=200)
    subcategories: list[str] | None = None
    status: str = Field(default="active", min_length=1, max_length=30)

    @field_validator("name", "slug", "description", "image", "status", mode="before")
    @classmethod
    def strip_category_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("subcategories", mode="before")
    @classmethod
    def normalize_subcategories(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            parts = [item.strip() for item in value.split("\n")]
            return [item for item in parts if item]
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or None
        return value


class AdminStoreUpsert(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=255)
    category: str = Field(min_length=2, max_length=120)
    audience_slugs: list[str] | None = None
    market_id: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=255)
    region: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    status: str = Field(default="active", min_length=1, max_length=30)

    @field_validator(
        "name",
        "slug",
        "description",
        "category",
        "market_id",
        "location",
        "region",
        "city",
        "status",
        mode="before",
    )
    @classmethod
    def strip_store_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("audience_slugs", mode="before")
    @classmethod
    def normalize_store_audiences(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",")]
            return [item for item in parts if item]
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or None
        return value


class AdminProductRead(BaseModel):
    id: str
    store_id: str | None
    store_name: str | None = None
    store_slug: str | None = None
    store_category: str | None = None
    store_location: str | None = None
    store_region: str | None = None
    store_city: str | None = None
    vendor_id: str | None
    vendor_name: str | None = None
    vendor_email: str | None = None
    name: str
    description: str
    images: list[str]
    image_key: str
    category: str
    subcategory: str | None = None
    category_slugs: list[str] | None = None
    subcategory_slugs: list[str] | None = None
    audience_slug: str | None = None
    section: str | None = None
    placement_tags: list[str] | None = None
    price: int
    old_price: int | None = None
    discount: str | None = None
    rating: float | None = None
    reviews: str | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None
    stock: int
    status: str
    created_at: datetime
    updated_at: datetime


class AdminProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str = Field(min_length=12, max_length=1000)
    category: str = Field(min_length=2, max_length=120)
    subcategory: str | None = Field(default=None, max_length=120)
    category_slugs: list[str] | None = None
    subcategory_slugs: list[str] | None = None
    store_id: str | None = Field(default=None, max_length=50)
    audience_slug: str | None = Field(default=None, max_length=50)
    section: str | None = Field(default=None, max_length=50)
    price: int = Field(ge=0)
    old_price: int | None = Field(default=None, ge=0)
    stock: int = Field(ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: str | None = Field(default=None, max_length=50)
    placement_tags: list[str] | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None
    status: str = Field(default="active", min_length=1, max_length=30)
    image_key: str | None = Field(default=None, max_length=100)

    @field_validator(
        "name",
        "description",
        "category",
        "subcategory",
        "store_id",
        "audience_slug",
        "section",
        "reviews",
        "status",
        "image_key",
        mode="before",
    )
    @classmethod
    def strip_product_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "placement_tags",
        "color_options",
        "size_options",
        "specifications",
        "category_slugs",
        "subcategory_slugs",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",")]
            return [item for item in parts if item]
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or None
        return value


class AdminProductStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminVoucherRead(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    issuer_name: str | None = None
    scope: str
    availability: str
    store_id: str | None = None
    store_name: str | None = None
    reward_text: str
    discount_type: str
    discount_value: float
    min_subtotal: float
    max_discount: float | None = None
    usage_limit: int | None = None
    per_user_limit: int | None = None
    is_active: bool
    status: str
    redemption_count: int
    unique_user_count: int
    total_discount_amount: float
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    created_at: datetime


class AdminVoucherUpsert(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=255)
    issuer_name: str | None = Field(default=None, max_length=120)
    scope: str = Field(default="odos", min_length=1, max_length=20)
    availability: str = Field(default="auto", min_length=1, max_length=20)
    store_id: str | None = Field(default=None, max_length=50)
    discount_type: str = Field(min_length=1, max_length=20)
    discount_value: float = Field(ge=0)
    min_subtotal: float = Field(default=0, ge=0)
    max_discount: float | None = Field(default=None, ge=0)
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    is_active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("title", "description", "issuer_name", "store_id", mode="before")
    @classmethod
    def strip_voucher_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("discount_type", "scope", "availability", mode="before")
    @classmethod
    def normalize_discount_type(cls, value: str) -> str:
        return value.strip().lower()


class AdminReviewRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    product_id: str
    product_name: str
    store_name: str | None = None
    user_id: uuid.UUID
    user_name: str
    user_email: str
    rating: float
    comment: str
    is_hidden: bool
    moderation_reason: str | None = None
    moderated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminReviewModerationUpdate(BaseModel):
    is_hidden: bool
    moderation_reason: str | None = Field(default=None, max_length=255)

    @field_validator("moderation_reason", mode="before")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AdminOrderRead(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_name: str
    store_name: str
    total_amount: float
    status: str
    payment_status: str
    created_at: datetime


class AdminOrderStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminNotificationRead(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    message: str
    read: bool
    created_at: datetime


class AdminDashboardRead(BaseModel):
    stats: AdminDashboardStatsRead
    recent_orders: list[AdminOrderRead]
    recent_vendor_applications: list[dict]
    recent_notifications: list[AdminNotificationRead]


class NotificationMarkReadResponse(BaseModel):
    success: bool = True
    notification_key: str


class AdminBootstrapStatusRead(BaseModel):
    bootstrap_enabled: bool
