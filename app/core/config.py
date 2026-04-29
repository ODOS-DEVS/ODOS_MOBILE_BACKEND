from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    google_client_ids: str = ""
    brevo_api_key: str = ""
    brevo_sender_name: str = "ODOS"
    brevo_sender_email: str = ""
    email_verification_code_expire_minutes: int = 10
    password_reset_code_expire_minutes: int = 10
    password_reset_token_expire_minutes: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def google_client_id_list(self) -> list[str]:
        return [item.strip() for item in self.google_client_ids.split(",") if item.strip()]

    @property
    def brevo_is_configured(self) -> bool:
        return bool(
            self.brevo_api_key.strip()
            and self.brevo_sender_name.strip()
            and self.brevo_sender_email.strip()
        )


settings = Settings()
