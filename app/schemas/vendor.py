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
    store_latitude: float | None = None
    store_longitude: float | None = None
    store_instagram_url: str | None = None
    store_facebook_url: str | None = None
    store_tiktok_url: str | None = None
    store_twitter_url: str | None = None
    store_whatsapp_url: str | None = None
    store_website_url: str | None = None
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
    available_balance: float = 0
    pending_withdrawal_balance: float = 0
    lifetime_earnings: float = 0
    total_commission: float = 0


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
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    instagram_url: str | None = None
    facebook_url: str | None = None
    tiktok_url: str | None = None
    twitter_url: str | None = None
    whatsapp_url: str | None = None
    website_url: str | None = None
    region: str
    city: str
    banner_image_url: str | None
    logo_image_url: str | None
    status: str


class VendorVoucherRead(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    issuer_name: str | None = None
    availability: str
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
    approval_status: str = "approved"
    campaign_tag: str | None = None
    review_notes: str | None = None
    created_at: datetime


class VendorVoucherUpsert(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=255)
    issuer_name: str | None = Field(default=None, max_length=120)
    availability: str = Field(default="claim", min_length=1, max_length=20)
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

    @field_validator("title", "description", "issuer_name", mode="before")
    @classmethod
    def strip_voucher_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("availability", "discount_type", mode="before")
    @classmethod
    def normalize_voucher_modes(cls, value: str) -> str:
        return value.strip().lower()


class VendorFlashSaleNominationCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    event_id: uuid.UUID | None = None
    proposed_price: int | None = Field(default=None, ge=1)
    proposed_old_price: int | None = Field(default=None, ge=1)
    stock_limit: int | None = Field(default=None, ge=1)
    max_per_user: int | None = Field(default=None, ge=1)
    vendor_note: str | None = Field(default=None, max_length=255)


class VendorFlashSaleNominationRead(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None = None
    event_title: str | None = None
    product_id: str
    product_title: str | None = None
    product_image_url: str | None = None
    proposed_price: int | None = None
    proposed_old_price: int | None = None
    stock_limit: int | None = None
    max_per_user: int | None = None
    vendor_note: str | None = None
    status: str
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class VendorVoucherGiftPayload(BaseModel):
    recipient_email: str = Field(min_length=3, max_length=255)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("recipient_email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class VendorWalletTransactionRead(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    amount: float
    gross_amount: float | None = None
    commission_amount: float | None = None
    balance_after: float
    order_id: uuid.UUID | None = None
    return_request_id: uuid.UUID | None = None
    withdrawal_request_id: uuid.UUID | None = None
    created_at: datetime


class VendorWithdrawalRequestRead(BaseModel):
    id: uuid.UUID
    status: str
    amount: float
    note: str | None = None
    admin_note: str | None = None
    payout_method_type: str
    payout_account_name: str
    payout_account_number_masked: str
    payout_provider: str | None = None
    transfer_failure_reason: str | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    paid_at: datetime | None = None
    transfer_initiated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class VendorWalletRead(BaseModel):
    id: uuid.UUID
    vendor_user_id: uuid.UUID
    currency: str
    available_balance: float
    pending_withdrawal_balance: float
    lifetime_earnings: float
    total_withdrawn: float
    total_commission: float
    payout_method_type: str | None = None
    payout_account_name: str | None = None
    payout_account_number_masked: str | None = None
    payout_provider: str | None = None
    recent_transactions: list[VendorWalletTransactionRead]
    withdrawal_requests: list[VendorWithdrawalRequestRead]


class VendorPayoutInstitutionRead(BaseModel):
    code: str
    name: str
    payout_method_type: str
    currency: str


class VendorWalletPayoutDetailsUpdate(BaseModel):
    payout_method_type: str = Field(min_length=2, max_length=30)
    payout_account_name: str = Field(min_length=2, max_length=120)
    payout_account_number: str = Field(min_length=4, max_length=80)
    payout_provider: str = Field(min_length=2, max_length=120)

    @field_validator(
        "payout_method_type",
        "payout_account_name",
        "payout_account_number",
        "payout_provider",
        mode="before",
    )
    @classmethod
    def strip_payout_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("payout_method_type", mode="after")
    @classmethod
    def normalize_payout_method(cls, value: str) -> str:
        return value.lower().replace(" ", "_")


class VendorWithdrawalCreate(BaseModel):
    amount: float = Field(gt=0)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("note", mode="before")
    @classmethod
    def strip_withdrawal_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class VendorProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(min_length=1, max_length=120)
    category_slug: str | None = Field(default=None, max_length=120)
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
    is_returnable: bool = True

    @field_validator(
        "name",
        "description",
        "category",
        "category_slug",
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
    category_slug: str | None = Field(default=None, max_length=120)
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
    is_returnable: bool | None = None
    status: str | None = Field(default=None, min_length=1, max_length=30)

    @field_validator(
        "name",
        "description",
        "category",
        "category_slug",
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


class VendorProductStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class VendorProductStockUpdate(BaseModel):
    stock: int = Field(ge=0)


class VendorProductRead(BaseModel):
    id: str
    store_id: str
    vendor_id: str
    name: str
    description: str
    category: str
    category_slug: str | None = None
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
    is_returnable: bool
    status: str
    created_at: datetime
    updated_at: datetime


class VendorOrderItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    quantity: int
    unit_price: float
    image_url: str | None = None


class VendorOrderRead(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_name: str | None
    customer_phone: str | None = None
    delivery_method: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_region: str | None = None
    payment_label: str | None = None
    product_count: int
    total_amount: float
    gross_amount: float | None = None
    commission_amount: float | None = None
    net_amount: float | None = None
    is_settled: bool = False
    currency: str = "GHS"
    status: str
    placed_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime
    items: list[VendorOrderItemRead]


class VendorReturnRequestRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    order_item_id: uuid.UUID
    product_id: str
    product_title: str
    product_image_url: str | None = None
    customer_name: str | None = None
    request_type: str
    status: str
    quantity: int
    reason: str
    details: str | None = None
    evidence_image_urls: list[str] | None = None
    admin_note: str | None = None
    refund_amount: float | None = None
    created_at: datetime
    updated_at: datetime


class VendorReturnRequestUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    vendor_note: str | None = Field(default=None, max_length=1000)

    @field_validator("status", "vendor_note", mode="before")
    @classmethod
    def normalize_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class VendorTopProductRead(BaseModel):
    product_id: str
    product_title: str
    product_image_url: str | None = None
    units_sold: int
    gross_sales: float


class VendorAnalyticsRead(BaseModel):
    currency: str = "GHS"
    today_sales: float
    week_sales: float
    today_orders: int
    week_orders: int
    open_returns: int
    top_products: list[VendorTopProductRead]
