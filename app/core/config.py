from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="agent_db", alias="POSTGRES_DB")
    postgres_user: str = Field(default="agent_user", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change_me", alias="POSTGRES_PASSWORD")
    database_url: str = Field(default="", alias="DATABASE_URL")
    session_encryption_key: str = Field(default="", alias="SESSION_ENCRYPTION_KEY")

    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    redis_url: str = Field(default="", alias="REDIS_URL")
    session_cache_ttl_seconds: int = Field(default=900, alias="SESSION_CACHE_TTL_SECONDS")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")

    platform_base_url: str = Field(default="", alias="PLATFORM_BASE_URL")
    platform_ws_url: str = Field(default="", alias="PLATFORM_WS_URL")
    platform_login: str = Field(default="", alias="PLATFORM_LOGIN")
    platform_password: str = Field(default="", alias="PLATFORM_PASSWORD")
    platform_access_token: str = Field(default="", alias="PLATFORM_ACCESS_TOKEN")
    platform_cf_bm_cookie: str = Field(default="", alias="PLATFORM_CF_BM_COOKIE")
    platform_cookie_header: str = Field(default="", alias="PLATFORM_COOKIE_HEADER")
    platform_claim_from_snapshot: bool = Field(default=False, alias="PLATFORM_CLAIM_FROM_SNAPSHOT")
    platform_take_burst_size: int = Field(default=1, alias="PLATFORM_TAKE_BURST_SIZE")
    platform_force_ipv4: bool = Field(default=True, alias="PLATFORM_FORCE_IPV4")
    platform_take_http1: bool = Field(default=False, alias="PLATFORM_TAKE_HTTP1")
    platform_take_health_enabled: bool = Field(default=True, alias="PLATFORM_TAKE_HEALTH_ENABLED")
    platform_take_health_interval_seconds: int = Field(
        default=5,
        alias="PLATFORM_TAKE_HEALTH_INTERVAL_SECONDS",
    )
    runtime_idle_ttl_seconds: int = Field(default=900, alias="RUNTIME_IDLE_TTL_SECONDS")
    runtime_cleanup_interval_seconds: int = Field(default=60, alias="RUNTIME_CLEANUP_INTERVAL_SECONDS")
    playwright_headless: bool = Field(default=True, alias="PLAYWRIGHT_HEADLESS")
    browser_runtime: str = Field(default="playwright", alias="BROWSER_RUNTIME")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
