from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"

    bot_token: str = Field(default="")
    webhook_secret: str = Field(default="")
    service_secret: str = Field(default="")
    init_data_max_age_seconds: int = 86400
    service_token_ttl_seconds: int = 60
    cors_allowed_origins: str = "https://web.telegram.org"

    database_url: str
    database_url_sync: str = ""
    redis_url: str
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    log_level: str = "INFO"
    sentry_dsn: str = ""

    admin_telegram_chat_id: str = ""
    owner_telegram_id: int = 0  # 0 = не задан, /admin/v1/* будет отвечать 503
    bot_token_admin: str = ""  # токен @PrideClubAdminBot, опционально в .env

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]