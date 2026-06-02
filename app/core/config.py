from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    cors_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:3000,"
        "http://127.0.0.1:3000"
    )
    media_root: str = "uploads"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    google_client_ids: str = ""
    brevo_api_key: str = ""
    brevo_sender_name: str = "ODOS"
    brevo_sender_email: str = ""
    email_verification_code_expire_minutes: int = 10
    phone_verification_code_expire_minutes: int = 10
    password_reset_code_expire_minutes: int = 10
    password_reset_token_expire_minutes: int = 15
    vendor_commission_rate: float = 0.10
    vendor_withdrawal_minimum: float = 20.0
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_currency: str = "GHS"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("postgres://"):
            return "postgresql+psycopg://" + normalized[len("postgres://") :]
        if normalized.startswith("postgresql://") and not normalized.startswith("postgresql+"):
            return "postgresql+psycopg://" + normalized[len("postgresql://") :]
        return normalized

    @property
    def google_client_id_list(self) -> list[str]:
        return [item.strip() for item in self.google_client_ids.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def brevo_is_configured(self) -> bool:
        return bool(
            self.brevo_api_key.strip()
            and self.brevo_sender_name.strip()
            and self.brevo_sender_email.strip()
        )

    @property
    def cloudinary_is_configured(self) -> bool:
        return bool(
            self.cloudinary_cloud_name.strip()
            and self.cloudinary_api_key.strip()
            and self.cloudinary_api_secret.strip()
        )

    @property
    def paystack_is_configured(self) -> bool:
        return bool(
            self.paystack_secret_key.strip()
            and self.paystack_public_key.strip()
        )


settings = Settings()
