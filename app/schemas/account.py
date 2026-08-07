import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AddressCreate(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    street: str = Field(min_length=3, max_length=255)
    gps_code: str | None = Field(default=None, max_length=32)
    city: str = Field(min_length=2, max_length=120)
    region: str = Field(min_length=2, max_length=120)
    is_default: bool = False

    @field_validator("label", "full_name", "phone", "street", "gps_code", "city", "region", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("gps_code")
    @classmethod
    def validate_gps_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper().replace(" ", "")
        if not cleaned:
            return None
        if len(cleaned) < 4:
            raise ValueError("Enter a valid GhanaPost GPS code.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone number must be at least 10 digits.")
        return value


class AddressUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, min_length=7, max_length=30)
    street: str | None = Field(default=None, min_length=3, max_length=255)
    gps_code: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, min_length=2, max_length=120)
    region: str | None = Field(default=None, min_length=2, max_length=120)
    is_default: bool | None = None

    @field_validator("label", "full_name", "phone", "street", "gps_code", "city", "region", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("phone")
    @classmethod
    def validate_optional_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone number must be at least 10 digits.")
        return value


class AddressRead(BaseModel):
    id: uuid.UUID
    label: str | None
    full_name: str
    phone: str
    street: str
    gps_code: str | None
    city: str
    region: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentMethodCreate(BaseModel):
    type: Literal["card", "momo"]
    label: str | None = Field(default=None, max_length=120)
    is_default: bool = False
    card_name: str | None = Field(default=None, max_length=120)
    # Client-truncated last 4 digits only — the app never transmits a full PAN,
    # so there's nothing here for the backend to reduce/tokenize.
    card_last4: str | None = Field(default=None, min_length=4, max_length=4)
    expiry: str | None = Field(default=None, max_length=10)
    network: Literal["MTN", "Telecel", "AT"] | None = None
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("label", "card_name", "card_last4", "expiry", "phone", mode="before")
    @classmethod
    def strip_payment_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("card_last4")
    @classmethod
    def validate_card_last4(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isdigit():
            raise ValueError("Card last 4 digits must be numeric.")
        return value

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if len(cleaned) != 5 or cleaned[2] != "/":
            raise ValueError("Expiry must use MM/YY format.")
        month = cleaned[:2]
        year = cleaned[3:]
        if not month.isdigit() or not year.isdigit():
            raise ValueError("Expiry must use MM/YY format.")
        month_number = int(month)
        if month_number < 1 or month_number > 12:
            raise ValueError("Expiry month must be between 01 and 12.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_payment_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone number must be at least 10 digits.")
        return value

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "card" and (not self.card_name or not self.card_last4 or not self.expiry):
            raise ValueError("Card name, last 4 digits, and expiry are required.")
        if self.type == "momo" and (not self.network or not self.phone):
            raise ValueError("Network and phone are required for mobile money.")
        return self


class PaymentMethodRead(BaseModel):
    id: uuid.UUID
    type: Literal["card", "momo"]
    label: str
    is_default: bool
    card_name: str | None
    card_last4: str | None
    expiry: str | None
    network: str | None
    phone: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
