import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import UserRole, VendorStatus


class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    phone_number: str | None = Field(default=None, max_length=30)

    @field_validator("full_name", "phone_number", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned_value = value.strip()
        return cleaned_value or None

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")

        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")

        return value


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(min_length=1)
    picture_url: str | None = Field(default=None, max_length=2000)


class VerifyEmailRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class SendPhoneVerificationRequest(BaseModel):
    phone_number: str = Field(min_length=7, max_length=30)
    link_to_profile: bool = True

    @field_validator("phone_number", mode="before")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        return value.strip()


class VerifyPhoneRequest(BaseModel):
    phone_number: str = Field(min_length=7, max_length=30)
    code: str = Field(pattern=r"^\d{6}$")
    link_to_profile: bool = True

    @field_validator("phone_number", mode="before")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        return value.strip()


class VerifiedPhonesResponse(BaseModel):
    phones: list[str]


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyPasswordResetCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class PasswordResetTokenResponse(BaseModel):
    message: str
    reset_token: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    reset_token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_bcrypt_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")

        return value


class WishlistItemCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=120)
    price: str | None = Field(default=None, max_length=50)
    old_price: str | None = Field(default=None, max_length=50)
    rating: str | None = Field(default=None, max_length=50)
    reviews: str | None = Field(default=None, max_length=50)

    @field_validator(
        "product_id",
        "title",
        "image_url",
        "category",
        "price",
        "old_price",
        "rating",
        "reviews",
        mode="before",
    )
    @classmethod
    def strip_wishlist_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned_value = value.strip()
        return cleaned_value or None


class WishlistItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    image_url: str | None
    category: str | None
    price: str | None
    old_price: str | None
    rating: str | None
    reviews: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CartItemCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=500)
    image_key: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=120)
    price: str = Field(min_length=1, max_length=50)
    quantity: int = Field(default=1, ge=1, le=99)

    @field_validator(
        "product_id",
        "title",
        "image_url",
        "image_key",
        "category",
        "price",
        mode="before",
    )
    @classmethod
    def strip_cart_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned_value = value.strip()
        return cleaned_value or None


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CartItemRead(BaseModel):
    id: uuid.UUID
    product_id: str
    title: str
    image_url: str | None
    image_key: str | None
    category: str | None
    price: str
    quantity: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone_number: str | None = Field(default=None, max_length=30)
    avatar_url: str | None = Field(default=None, max_length=500)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    allow_notifications: bool | None = None
    discount_notifications: bool | None = None
    store_notifications: bool | None = None
    vendor_order_notifications: bool | None = None
    system_notifications: bool | None = None
    location_notifications: bool | None = None
    location_updates: bool | None = None
    personalization_enabled: bool | None = None
    analytics_enabled: bool | None = None

    @field_validator(
        "full_name",
        "phone_number",
        "avatar_url",
        "gender",
        "city",
        "region",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value

        cleaned_value = value.strip()
        return cleaned_value or None


class UserRead(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    phone_number: str | None
    avatar_url: str | None
    date_of_birth: date | None
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
    personalization_enabled: bool
    analytics_enabled: bool
    role: UserRole
    admin_permission: str | None = None
    roles: list[str]
    vendor_status: VendorStatus
    vendor_id: str | None
    vendor_rejection_reason: str | None
    is_active: bool
    is_verified: bool
    phone_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead


class LogoutResponse(BaseModel):
    message: str


class MessageResponse(BaseModel):
    message: str
