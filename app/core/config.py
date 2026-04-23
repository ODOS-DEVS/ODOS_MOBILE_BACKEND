from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    google_client_ids: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def google_client_id_list(self) -> list[str]:
        return [item.strip() for item in self.google_client_ids.split(",") if item.strip()]


settings = Settings()
