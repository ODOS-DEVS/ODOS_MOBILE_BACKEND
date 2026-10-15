from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def settings_secret_is_live(secret_key: str) -> bool:
    """True only for a key that is positively identifiable as live.

    Anything unset, malformed or unrecognised reads as not-live. A key we
    cannot classify must not be assumed to move real money.
    """
    return (secret_key or "").strip().startswith("sk_live_")


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
    admin_panel_url: str = "https://odos-admin.onrender.com"
    email_verification_code_expire_minutes: int = 10
    phone_verification_code_expire_minutes: int = 10
    arkesel_api_key: str = ""
    arkesel_sender_id: str = "ODOS"
    arkesel_otp_message: str = "Your ODOS verification code is %otp_code%. Do not share it."
    password_reset_code_expire_minutes: int = 10
    password_reset_token_expire_minutes: int = 15
    vendor_commission_rate: float = 0.10
    vendor_withdrawal_minimum: float = 20.0

    # How long after delivery a customer may open a return. Without a limit a
    # request can arrive a year later, by which point the goods, the vendor's
    # settlement and any evidence are long gone.
    return_window_days: int = 14
    # A vendor may settle small refunds themselves; above this an admin has to
    # approve. Protects against a compromised seller account draining value,
    # and gives ODOS sight of the expensive cases.
    vendor_self_refund_limit: float = 200.0
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_currency: str = "GHS"
    paystack_payouts_enabled: bool = True
    # Allows wallet top-ups against a TEST Paystack key. Only ever correct on a
    # staging environment, where crediting a wallet without real money is the
    # point. In production this hands out free spendable balance -- see
    # wallet_topups_are_allowed.
    wallet_topups_allow_test_gateway: bool = False
    # KutanaPay collects payments only -- its public API covers checkouts and
    # nothing else, so Paystack stays responsible for vendor transfers.
    kutanapay_api_key: str = ""
    kutanapay_webhook_secret: str = ""
    # The API host, and the default below is the SANDBOX one.
    #
    # Established by calling it: a key issued in the live portal is rejected
    # there with 401 {"detail":"Invalid API key"}, while the older key succeeds
    # and returns checkout_url values on dev.kutanapay.com. Sandbox API,
    # sandbox checkout page. Checkouts created against it never deliver a real
    # mobile money prompt -- they sit at "Waiting" until they expire, with no
    # error anywhere, which is an expensive thing to diagnose twice.
    #
    # dev.kutanapay.com is not the API host either, despite their docs naming
    # it as the sandbox one: it serves the dashboard and the customer-facing
    # checkout page, and answers API calls with an HTML 404.
    #
    # The production host is not known -- KutanaPay publish no developer docs
    # and it is not discoverable. Going live means setting this to whatever
    # they say alongside the live key; neither works without the other.
    kutanapay_base_url: str = "https://api.kutanapay.com"
    kutanapay_currency: str = "GHS"
    redis_url: str = ""
    rate_limit_enabled: bool = True
    cache_enabled: bool = True
    db_pool_size: int = 3
    db_max_overflow: int = 7
    db_pool_timeout: int = 20
    # Neon closes idle connections at its proxy well before 30 minutes, so a
    # connection recycled at 5 minutes is never handed to a query after the far
    # end has already gone away. pool_pre_ping catches the rest.
    db_pool_recycle: int = 300
    # Run the five periodic jobs as in-process asyncio loops in the API process.
    #
    # True (the default) preserves the existing behaviour exactly. Set to FALSE when running
    # the Celery worker + beat pair, which schedules the same five jobs — leaving both enabled
    # runs every job twice. See app/core/celery_app.py.
    scheduler_enabled: bool = True

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

    @property
    def paystack_is_live(self) -> bool:
        """Whether Paystack would move real money.

        Paystack states its mode in the key prefix, which is the only reliable
        signal -- a test key and a live key are otherwise identical in shape.
        """
        return settings_secret_is_live(self.paystack_secret_key)

    @property
    def wallet_topups_are_allowed(self) -> bool:
        """Wallet top-ups must not run on a test gateway in a live app.

        Not a style rule -- a test-mode top-up credits a real wallet balance.
        Paystack's test cards are published in their own documentation, so the
        chain is: top up with a public test card, Paystack test verifies it as
        a success, `_reconcile_wallet_topup` adds the amount to
        `available_balance`, and that balance then buys real goods from real
        vendors through `create_wallet_checkout`. Free money, and a vendor owed
        a real settlement against none.

        So top-ups are gated on Paystack actually being live.
        `WALLET_TOPUPS_ALLOW_TEST_GATEWAY=true` forces them on for a staging
        environment where fake credit is the point. It has to be set
        deliberately, which an `sk_test_` key left in place does not.
        """
        if self.wallet_topups_allow_test_gateway:
            return True
        return self.paystack_is_live

    @property
    def kutanapay_is_configured(self) -> bool:
        # The webhook secret is not required to create a checkout, so it is not
        # checked here -- but without it signatures cannot be verified, which
        # the webhook route refuses on separately.
        return bool(self.kutanapay_api_key.strip())

    assistant_enabled: bool = True
    assistant_provider: str = "gemini"
    assistant_model: str = "gemini-3.1-flash-lite"
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
            return self.assistant_model.strip() or "gemini-3.1-flash-lite"
        return self.assistant_model.strip() or "gemini-3.1-flash-lite"

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
