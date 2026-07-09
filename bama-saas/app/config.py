from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Bama Insight API"
    database_url: str = "postgresql+psycopg://bama:bama@localhost:5432/bama_insight"
    admin_api_key: str = Field(default="change-me", min_length=8)
    bama_max_ads: int = Field(default=50_000, ge=1)
    bama_page_pause: float = Field(default=0.8, ge=0)
    bama_request_timeout: int = Field(default=20, ge=1)
    bama_cookie: str = ""
    stale_after_days: int = Field(default=14, ge=1)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
