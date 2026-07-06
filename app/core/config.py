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
        "http://127.0.0.1:3000,"
        "http://localhost:4173,"
        "http://127.0.0.1:4173,"
        "https://odos-admin.onrender.com"
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
    arkesel_api_key: str = ""
    arkesel_sender_id: str = "ODOS"
    arkesel_otp_message: str = "Your ODOS verification code is %otp_code%. Do not share it."
    password_reset_code_expire_minutes: int = 10
    password_reset_token_expire_minutes: int = 15
    vendor_commission_rate: float = 0.10
    vendor_withdrawal_minimum: float = 20.0
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_currency: str = "GHS"
    redis_url: str = ""
    rate_limit_enabled: bool = True
    cache_enabled: bool = True

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

    @field_validator("redis_url", mode="before")
    @classmethod
    def normalize_redis_url(cls, value: str | None) -> str:
        if not value:
            return ""

        normalized = str(value).strip().strip('"').strip("'")
        if normalized.upper().startswith("REDIS_URL="):
            normalized = normalized.split("=", 1)[1].strip()
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
    def arkesel_is_configured(self) -> bool:
        return bool(
            self.arkesel_api_key.strip()
            and self.arkesel_sender_id.strip()
            and "%otp_code%" in self.arkesel_otp_message
        )

    @property
    def rate_limit_is_active(self) -> bool:
        return self.rate_limit_enabled and bool(self.redis_url.strip())

    @property
    def cache_is_active(self) -> bool:
        return self.cache_enabled and bool(self.redis_url.strip())

    @property
    def paystack_is_configured(self) -> bool:
        return bool(
            self.paystack_secret_key.strip()
            and self.paystack_public_key.strip()
        )

    assistant_enabled: bool = True
    assistant_provider: str = "gemini"
    assistant_model: str = "gemini-2.0-flash-lite"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_base_url: str = "http://127.0.0.1:11434"
    openai_api_key: str = ""
    openai_assistant_model: str = "gpt-4o-mini"

    @property
    def assistant_provider_normalized(self) -> str:
        value = self.assistant_provider.strip().lower()
        if value in {"gemini", "openrouter", "ollama", "openai"}:
            return value
        return "gemini"

    @property
    def assistant_model_name(self) -> str:
        if self.assistant_provider_normalized == "openai":
            return self.openai_assistant_model.strip() or self.assistant_model.strip()
        if self.assistant_provider_normalized == "gemini":
            return self.assistant_model.strip() or "gemini-2.0-flash-lite"
        return self.assistant_model.strip() or "gemini-2.0-flash-lite"

    @property
    def gemini_api_base(self) -> str:
        return (
            self.gemini_base_url.strip().rstrip("/")
            or "https://generativelanguage.googleapis.com/v1beta"
        )

    @property
    def openrouter_api_base(self) -> str:
        url = self.openrouter_base_url.strip().rstrip("/") or "https://openrouter.ai/api/v1"
        if url.endswith("/api"):
            return f"{url}/v1"
        if "/api/v1" not in url and "openrouter.ai" in url:
            return "https://openrouter.ai/api/v1"
        return url

    @property
    def assistant_is_configured(self) -> bool:
        if not self.assistant_enabled:
            return False
        provider = self.assistant_provider_normalized
        if provider == "gemini":
            return bool(self.gemini_api_key.strip())
        if provider == "openrouter":
            return bool(self.openrouter_api_key.strip())
        if provider == "ollama":
            return bool(self.ollama_base_url.strip())
        return bool(self.openai_api_key.strip())


settings = Settings()
