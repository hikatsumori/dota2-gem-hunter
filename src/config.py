
"""
Configuration for Dota 2 Steam Market Rare Socket Scanner.
Conservative settings for low-frequency market monitoring.
"""

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    supabase_db_url: str = Field(
        default="",
        validation_alias="SUPABASE_DB_URL",
    )

    database_url: str = Field(
        default="",
        validation_alias="DATABASE_URL",
    )

    steam_webapi_key: str = Field(
        default="",
        validation_alias="STEAM_WEBAPI_KEY",
    )

    telegram_bot_token: str = Field(
        default="",
        validation_alias="TELEGRAM_BOT_TOKEN",
    )

    telegram_chat_id: str = Field(
        default="",
        validation_alias="TELEGRAM_CHAT_ID",
    )

    github_actions: bool = Field(
        default=False,
        validation_alias="GITHUB_ACTIONS",
    )

    @property
    def db_url(self) -> str:
        return self.supabase_db_url or self.database_url


settings = Settings()


SCAN_CONFIG: dict[str, Any] = {
    # Only categories supported by the original project
    "item_types": [
        "courier",
        "ward",
        "announcer",
        "hud",
        "loading_screen",
    ],

    # Conservative scanning limits
    "max_pages_per_type": 1,
    "request_delay_seconds": 15,
    "max_retries": 0,

    # Steam configuration
    "steam_app_id": 570,
    "market_search_url": "https://steamcommunity.com/market/search/render/",
    "listing_base_url": "https://steamcommunity.com/market/listings/570/",
    "user_agent": "Mozilla/5.0 (compatible; Dota2GemScanner/1.0)",
}

ALERT_RULES: list[dict[str, Any]] = [
    {
        "name": "Ethereal Gem Under $100",
        "gem_types": ["ethereal"],
        "max_price_usd": 100.0,
        "min_discount_pct": 30.0,
    },
    {
        "name": "Prismatic Gem Under $150",
        "gem_types": ["prismatic"],
        "max_price_usd": 150.0,
        "min_discount_pct": 25.0,
    },
    {
        "name": "Any Rare Socket Deep Discount",
        "gem_types": ["ethereal", "prismatic"],
        "max_price_usd": 500.0,
        "min_discount_pct": 50.0,
    },
]

GEM_DATABASE_PATH = (
    Path(__file__).parent.parent / "data" / "gems.json"
)

ALEMBIC_MIGRATIONS_PATH = (
    Path(__file__).parent / "database" / "migrations"
)

RETENTION_DAYS = 30


def validate_required_env() -> list[str]:
    """Return missing required environment variables."""

    missing = []

    if not settings.db_url:
        missing.append("SUPABASE_DB_URL (or DATABASE_URL)")

    if not settings.steam_webapi_key:
        missing.append("STEAM_WEBAPI_KEY")

    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not settings.telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID")

    return missing


def get_db_url() -> str:
    """Get the configured database URL."""

    url = settings.db_url

    if not url:
        raise RuntimeError(
            "Database URL not configured. "
            "Set SUPABASE_DB_URL or DATABASE_URL."
        )

    return url
