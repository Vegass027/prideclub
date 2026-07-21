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

    bot_token: str = Field(default="")
    webhook_secret: str = Field(default="")
    webhook_base_url: str = Field(default="")
    webhook_path: str = "/bot/webhook"
    webapp_url: str = Field(default="")
    backend_url: str = "http://backend:8000"
    service_secret: str = Field(default="")
    log_level: str = "INFO"
    environment: str = "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # noqa