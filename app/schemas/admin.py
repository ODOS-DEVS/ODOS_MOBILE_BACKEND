from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models import VendorStatus
from app.schemas.payment import AdminPaymentTransactionRead


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
    admin_permission: str | None = None
    vendor_status: VendorStatus
    account_status: str
    joined_at: datetime


class AdminUserAddressRead(BaseModel):
    id: uuid.UUID
    label: str | None
    full_name: str
    phone: str
    street: str
    city: str
    region: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AdminUserPaymentMethodRead(BaseModel):
    id: uuid.UUID
    type: str
    label: str
    is_default: bool
    card_name: str | None
    card_last4: str | None
    expiry: str | None
    network: str | None
    phone: str | None
    created_at: datetime
    updated_at: datetime


class AdminUserStoreSummaryRead(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    logo_image: str | None
    banner_image: str | None
    market_id: str | None
    location: str | None
    region: str
    city: str
    created_at: datetime
    updated_at: datetime


class AdminUserVendorApplicationRead(BaseModel):
    id: uuid.UUID
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


class AdminUserStatsRead(BaseModel):
    total_orders: int
    total_reviews: int
    total_saved_addresses: int
    total_saved_payment_methods: int
    total_cart_items: int
    total_wishlist_items: int
    total_notifications: int
    total_spent: float
    last_order_at: datetime | None
    last_review_at: datetime | None


class AdminUserCartItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    image_url: str | None = None
    category: str | None = None
    price: str
    quantity: int
    created_at: datetime
    updated_at: datetime


class AdminUserWishlistItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    image_url: str | None = None
    category: str | None = None
    price: str | None = None
    created_at: datetime


class AdminUserNotificationRead(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    message: str
    created_at: datetime


class AdminUserWalletSummaryRead(BaseModel):
    balance: float
    currency: str
    lifetime_topups: float
    lifetime_spend: float
    lifetime_refunds: float
    transaction_count: int


class AdminUserDetailRead(AdminUserRead):
    date_of_birth: date | None = None
    gender: str | None
    city: str | None
    region: str | None
    allow_notifications: bool
    discount_notifications: bool
    store_notifications: bool
    vendor_order_notifications: bool
    system_notifications: bool
    location_notifications: bool
    location_updates: bool
    personalization_enabled: bool = True
    analytics_enabled: bool = True
    phone_verified: bool = False
    vendor_rejection_reason: str | None
    is_verified: bool
    last_login_at: datetime | None
    updated_at: datetime
    auth_providers: list[str]
    addresses: list[AdminUserAddressRead]
    payment_methods: list[AdminUserPaymentMethodRead]
    vendor_application: AdminUserVendorApplicationRead | None = None
    stores: list[AdminUserStoreSummaryRead]
    stats: AdminUserStatsRead
    orders: list[AdminOrderRead] = Field(default_factory=list)
    reviews: list[AdminReviewRead] = Field(default_factory=list)
    return_requests: list[AdminReturnRequestRead] = Field(default_factory=list)
    payment_transactions: list[AdminPaymentTransactionRead] = Field(default_factory=list)
    cart_items: list[AdminUserCartItemRead] = Field(default_factory=list)
    wishlist_items: list[AdminUserWishlistItemRead] = Field(default_factory=list)
    notifications: list[AdminUserNotificationRead] = Field(default_factory=list)
    customer_wallet: AdminUserWalletSummaryRead | None = None
    behavior_event_count: int = 0


class AdminUserStatusUpdate(BaseModel):
    account_status: str = Field(min_length=1, max_length=30)

    @field_validator("account_status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminPermissionUpdate(BaseModel):
    admin_permission: str = Field(min_length=1, max_length=30)

    @field_validator("admin_permission", mode="before")
    @classmethod
    def normalize_permission(cls, value: str) -> str:
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


class AdminStoreProductRead(BaseModel):
    id: str
    name: str
    status: str
    price: int
    old_price: int | None = None
    discount: str | None = None
    stock: int
    category: str
    subcategory: str | None = None
    images: list[str]
    created_at: datetime
    updated_at: datetime


class AdminStoreStatsRead(BaseModel):
    total_products: int
    active_products: int
    pending_products: int
    hidden_products: int
    total_orders: int
    total_sales: float


class AdminStoreDetailRead(AdminStoreRead):
    vendor_name: str | None = None
    vendor_email: str | None = None
    vendor_phone_number: str | None = None
    market_name: str | None = None
    updated_at: datetime
    products: list[AdminStoreProductRead]
    stats: AdminStoreStatsRead


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
    approval_status: str = "approved"
    campaign_tag: str | None = None
    review_notes: str | None = None
    created_by_user_id: uuid.UUID | None = None
    promotion_type: str = "coupon"
    priority: int = 0
    stackable: bool = False
    exclusive_group: str | None = None
    auto_apply: bool = False
    bogo_buy_quantity: int | None = None
    bogo_get_quantity: int | None = None
    bogo_get_discount_percent: float | None = None
    first_order_only: bool = False
    new_user_only: bool = False
    category_slugs: list[str] | None = None
    product_ids: list[str] | None = None
    excluded_product_ids: list[str] | None = None


class AdminVoucherReview(BaseModel):
    approval_status: str = Field(min_length=1, max_length=20)
    review_notes: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @field_validator("approval_status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


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
    campaign_tag: str | None = Field(default=None, max_length=50)
    promotion_type: str = Field(default="coupon", max_length=30)
    priority: int = Field(default=0, ge=0, le=1000)
    stackable: bool = False
    exclusive_group: str | None = Field(default=None, max_length=50)
    auto_apply: bool = False
    bogo_buy_quantity: int | None = Field(default=None, ge=1)
    bogo_get_quantity: int | None = Field(default=None, ge=1)
    bogo_get_discount_percent: float | None = Field(default=100, ge=0, le=100)
    first_order_only: bool = False
    new_user_only: bool = False
    category_slugs: list[str] | None = None
    product_ids: list[str] | None = None
    excluded_product_ids: list[str] | None = None

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

    @field_validator("promotion_type", mode="before")
    @classmethod
    def normalize_promotion_type(cls, value: str) -> str:
        return value.strip().lower()


class AdminVoucherBulkGenerate(BaseModel):
    prefix: str = Field(min_length=2, max_length=20)
    count: int = Field(default=10, ge=1, le=200)
    template: AdminVoucherUpsert

    @field_validator("prefix", mode="before")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        return value.strip().upper()


class AdminPromotionAnalyticsRead(BaseModel):
    total_campaigns: int
    active_campaigns: int
    total_redemptions: int
    total_discount_given: float
    top_campaigns: list[AdminVoucherRead]


class AdminFlashSaleNominationRead(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None = None
    event_title: str | None = None
    product_id: str
    product_title: str | None = None
    vendor_user_id: uuid.UUID
    proposed_price: int | None = None
    proposed_old_price: int | None = None
    stock_limit: int | None = None
    max_per_user: int | None = None
    vendor_note: str | None = None
    status: str
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminFlashSaleNominationReview(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    review_notes: str | None = Field(default=None, max_length=255)
    event_id: uuid.UUID | None = None
    flash_sale_price: int | None = Field(default=None, ge=1)
    flash_sale_old_price: int | None = Field(default=None, ge=1)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
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


class AdminOrderItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    category: str | None
    image_url: str | None
    image_key: str | None
    quantity: int
    unit_price: float
    line_total: float
    selected_color: str | None
    selected_size: str | None


class AdminReturnRequestRead(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    order_item_id: uuid.UUID
    product_id: str
    product_title: str
    product_image_url: str | None
    product_image_key: str | None
    store_name: str
    user_id: uuid.UUID
    customer_name: str
    customer_email: str
    request_type: str
    status: str
    quantity: int
    reason: str
    details: str | None
    evidence_image_urls: list[str] | None
    admin_note: str | None
    refund_amount: float | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminOrderDetailRead(AdminOrderRead):
    customer_id: uuid.UUID
    customer_email: str
    customer_phone_number: str | None
    customer_avatar_url: str | None
    source: str
    internal_status: str
    vendor_status: str
    subtotal_amount: float
    shipping_amount: float
    discount_amount: float
    delivery_method: str
    delivery_method_label: str
    progress: float | None
    tracking_eta: str | None
    cancellation_reason: str | None
    address_full_name: str
    address_phone: str
    address_street: str
    address_city: str
    address_region: str
    payment_type: str
    payment_label: str
    payment_provider: str
    payment_reference: str | None
    payment_network: str | None
    payment_phone: str | None
    payment_last4: str | None
    voucher_id: uuid.UUID | None
    voucher_code: str | None
    voucher_title: str | None
    placed_at: datetime
    paid_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    refunded_at: datetime | None
    updated_at: datetime
    items: list[AdminOrderItemRead]
    return_requests: list[AdminReturnRequestRead]


class AdminOrderStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        return value.strip().lower()


class AdminReturnRequestUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    admin_note: str | None = Field(default=None, max_length=1000)
    refund_amount: float | None = Field(default=None, ge=0)

    @field_validator("status", "admin_note", mode="before")
    @classmethod
    def normalize_return_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AdminVendorWithdrawalRequestRead(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    vendor_user_id: uuid.UUID
    vendor_name: str
    vendor_email: str
    store_name: str | None = None
    currency: str
    status: str
    amount: float
    note: str | None = None
    admin_note: str | None = None
    payout_method_type: str
    payout_account_name: str
    payout_account_number_masked: str
    payout_provider: str | None = None
    paystack_transfer_reference: str | None = None
    paystack_transfer_code: str | None = None
    transfer_failure_reason: str | None = None
    transfer_initiated_at: datetime | None = None
    wallet_available_balance: float
    wallet_pending_withdrawal_balance: float
    reviewed_by_user_id: uuid.UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminVendorWithdrawalUpdate(BaseModel):
    status: str = Field(min_length=2, max_length=30)
    admin_note: str | None = Field(default=None, max_length=255)
    confirm_manual_payout: bool = False

    @field_validator("status", mode="before")
    @classmethod
    def normalize_withdrawal_status(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("admin_note", mode="before")
    @classmethod
    def strip_withdrawal_admin_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


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


class AdminPromoBannerRead(BaseModel):
    id: uuid.UUID
    title: str
    subtitle: str | None = None
    cta_label: str
    cta_link: str | None = None
    image_url: str | None = None
    accent: str | None = None
    sort_order: int
    status: str
    link_type: str
    campaign_tag: str | None = None
    placement: str
    destination_label: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminPromoBannerUpsert(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    subtitle: str | None = Field(default=None, max_length=255)
    cta_label: str = Field(default="Shop now", min_length=2, max_length=80)
    cta_link: str | None = Field(default=None, max_length=500)
    accent: str | None = Field(default=None, max_length=20)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    status: str = Field(default="active", min_length=1, max_length=30)
    link_type: str = Field(default="deals", min_length=1, max_length=30)
    campaign_tag: str | None = Field(default=None, max_length=50)
    placement: str = Field(default="home", min_length=1, max_length=30)
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator(
        "title",
        "subtitle",
        "cta_label",
        "cta_link",
        "accent",
        "status",
        "link_type",
        "campaign_tag",
        "placement",
        mode="before",
    )
    @classmethod
    def strip_promo_banner_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AdminFlashSaleEventRead(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None = None
    image_url: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime
    sort_order: int
    status: str
    product_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AdminFlashSaleEventUpsert(BaseModel):
    slug: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=120)
    subtitle: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    status: str = Field(default="active", min_length=1, max_length=30)
    starts_at: datetime | None = None
    ends_at: datetime
    product_ids: list[str] = Field(default_factory=list)

    @field_validator("slug", "title", "subtitle", "status", mode="before")
    @classmethod
    def strip_flash_sale_event_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("product_ids", mode="before")
    @classmethod
    def normalize_product_ids(cls, value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]
