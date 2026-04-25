import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import UserRole


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


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone_number: str | None = Field(default=None, max_length=30)
    avatar_url: str | None = Field(default=None, max_length=500)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)

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
    role: UserRole
    is_active: bool
    is_verified: bool
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
