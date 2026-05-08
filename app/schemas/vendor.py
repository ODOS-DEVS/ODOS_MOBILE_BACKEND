import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import VendorStatus


class VendorApplicationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: VendorStatus
    business_name: str
    business_category: str
    business_description: str
    phone_number: str
    whatsapp_number: str | None
    region: str
    city: str
    market_id: str | None
    store_location: str | None
    store_name: str
    store_description: str | None
    ghana_card_number: str | None
    business_registration_number: str | None
    logo_image_url: str | None
    banner_image_url: str | None
    shop_image_url: str | None
    rejection_reason: str | None
    reviewed_at: datetime | None
    submitted_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VendorApplicationListItem(VendorApplicationRead):
    full_name: str
    email: str


class VendorApplicationReviewPayload(BaseModel):
    rejection_reason: str | None = Field(default=None, max_length=255)

    @field_validator("rejection_reason", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class VendorProfileRead(BaseModel):
    id: str
    user_id: uuid.UUID
    status: VendorStatus
    business_name: str
    business_category: str
    business_description: str
    phone_number: str
    whatsapp_number: str | None
    created_at: datetime
    store_id: str | None
    store_name: str | None
    rejection_reason: str | None


class VendorDashboardRead(BaseModel):
    store_name: str
    vendor_status: VendorStatus
    total_products: int
    active_products: int
    pending_orders: int
    completed_orders: int
    total_sales: float
    currency: str = "GHS"


class VendorStoreRead(BaseModel):
    id: str
    vendor_id: str
    name: str
    slug: str
    description: str
    category: str
    audience_slugs: list[str] | None = None
    market_id: str | None
    market_slug: str | None
    location: str | None
    region: str
    city: str
    banner_image_url: str | None
    logo_image_url: str | None
    status: str


class VendorProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(min_length=1, max_length=120)
    subcategory: str | None = Field(default=None, max_length=120)
    price: int = Field(ge=0)
    old_price: int | None = Field(default=None, ge=0)
    stock: int = Field(ge=0)
    image_key: str = Field(default="bag", min_length=1, max_length=100)
    image_url: str | None = Field(default=None, max_length=500)
    image_urls: list[str] | None = None
    placement_tags: list[str] | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None

    @field_validator(
        "name",
        "description",
        "category",
        "subcategory",
        "image_key",
        "image_url",
        mode="before",
    )
    @classmethod
    def strip_product_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "image_urls",
        "placement_tags",
        "color_options",
        "size_options",
        "specifications",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",")]
            return [item for item in parts if item]
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or None
        return value


class VendorProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    category: str | None = Field(default=None, min_length=1, max_length=120)
    subcategory: str | None = Field(default=None, max_length=120)
    price: int | None = Field(default=None, ge=0)
    old_price: int | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    image_key: str | None = Field(default=None, min_length=1, max_length=100)
    image_url: str | None = Field(default=None, max_length=500)
    image_urls: list[str] | None = None
    placement_tags: list[str] | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None
    status: str | None = Field(default=None, min_length=1, max_length=30)

    @field_validator(
        "name",
        "description",
        "category",
        "subcategory",
        "image_key",
        "image_url",
        "status",
        mode="before",
    )
    @classmethod
    def strip_product_update_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "image_urls",
        "placement_tags",
        "color_options",
        "size_options",
        "specifications",
        mode="before",
    )
    @classmethod
    def normalize_update_lists(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            parts = [item.strip() for item in value.split(",")]
            return [item for item in parts if item]
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return cleaned or None
        return value


class VendorOrderStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)


class VendorProductRead(BaseModel):
    id: str
    store_id: str
    vendor_id: str
    name: str
    description: str
    category: str
    subcategory: str | None = None
    price: int
    old_price: int | None = None
    discount: str | None = None
    stock: int
    image_key: str
    image_url: str | None
    image_urls: list[str] | None = None
    placement_tags: list[str] | None = None
    color_options: list[str] | None = None
    size_options: list[str] | None = None
    specifications: list[str] | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class VendorOrderItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    quantity: int
    unit_price: float


class VendorOrderRead(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_name: str | None
    product_count: int
    total_amount: float
    status: str
    created_at: datetime
    items: list[VendorOrderItemRead]
