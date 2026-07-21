from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = ""
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    bot_token: str = ""
    service_secret: str = ""
    log_level: str = "INFO"
    sentry_dsn: str = ""
    environment: str = "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import os

    s = Settings()
    if not s.database_url:
        s.database_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://habits:habits@postgres:5432/habits"
        )
    return s