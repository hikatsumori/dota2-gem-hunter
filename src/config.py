"""
Configuration module for Dota 2 Steam Market Rare Socket Scanner.

All configuration is centralized here. Environment variables are used for secrets
(Supabase URL, Steam WebAPI key, Discord webhook), while scan configuration
and alert rules are hardcoded per the design spec for version control.
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

    # Supabase / Database
    supabase_db_url: str = Field(
        default="",
        description="Supabase PostgreSQL connection URL (postgresql://...)",
        validation_alias="SUPABASE_DB_URL",
    )
    database_url: str = Field(
        default="",
        description="Fallback database URL if SUPABASE_DB_URL not set",
        validation_alias="DATABASE_URL",
    )

    # Steam WebAPI
    steam_webapi_key: str = Field(
        default="",
        description="Steam WebAPI key for GetSchema endpoint",
        validation_alias="STEAM_WEBAPI_KEY",
    )

    # Discord
    discord_webhook_url: str = Field(
        default="",
        description="Discord webhook URL for alert notifications",
        validation_alias="DISCORD_WEBHOOK_URL",
    )

    # Optional: GitHub Actions environment detection
    github_actions: bool = Field(
        default=False,
        description="Running inside GitHub Actions",
        validation_alias="GITHUB_ACTIONS",
    )

    @property
    def db_url(self) -> str:
        """Get the database URL, preferring SUPABASE_DB_URL over DATABASE_URL."""
        return self.supabase_db_url or self.database_url


# Global settings instance
settings = Settings()


# ──────────────────────────────────────────────────────────────────────────────
# Hardcoded Scan Configuration (per design spec)
# ──────────────────────────────────────────────────────────────────────────────

SCAN_CONFIG: dict[str, Any] = {
    "item_types": ["courier", "ward", "announcer", "hud", "loading_screen", "emoticon", "chat_wheel", "music_pack"],
    "max_pages_per_type": 5,           # ~100 listings per type
    "request_delay_seconds": 3,        # Rate limiting: 3s between requests
    "max_retries": 3,
    "steam_app_id": 570,               # Dota 2 app ID
    "market_search_url": "https://steamcommunity.com/market/search/render/",
    "listing_base_url": "https://steamcommunity.com/market/listings/570/",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# ──────────────────────────────────────────────────────────────────────────────
# Hardcoded Alert Rules (per design spec)
# ──────────────────────────────────────────────────────────────────────────────

ALERT_RULES: list[dict[str, Any]] = [
    {
        "name": "Ethereal Gem Under $100",
        "gem_types": ["ethereal"],
        "max_price_usd": 100.0,
        "min_discount_pct": 30.0,  # 30% below average
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

# ──────────────────────────────────────────────────────────────────────────────
# Gem Database Path
# ──────────────────────────────────────────────────────────────────────────────

GEM_DATABASE_PATH = Path(__file__).parent.parent / "data" / "gems.json"

# ──────────────────────────────────────────────────────────────────────────────
# Database / Alembic
# ──────────────────────────────────────────────────────────────────────────────

ALEMBIC_MIGRATIONS_PATH = Path(__file__).parent / "database" / "migrations"

# ──────────────────────────────────────────────────────────────────────────────
# Retention Policy
# ──────────────────────────────────────────────────────────────────────────────

RETENTION_DAYS = 30  # Auto-cleanup after 30 days

# ──────────────────────────────────────────────────────────────────────────────
# Validation Helpers
# ──────────────────────────────────────────────────────────────────────────────

def validate_required_env() -> list[str]:
    """
    Validate that all required environment variables are set.
    Returns list of missing variable names (empty if all present).
    """
    missing = []
    if not settings.db_url:
        missing.append("SUPABASE_DB_URL (or DATABASE_URL)")
    if not settings.steam_webapi_key:
        missing.append("STEAM_WEBAPI_KEY")
    if not settings.discord_webhook_url:
        missing.append("DISCORD_WEBHOOK_URL")
    return missing


def get_db_url() -> str:
    """Get the database URL, raising if not configured."""
    url = settings.db_url
    if not url:
        raise RuntimeError(
            "Database URL not configured. Set SUPABASE_DB_URL or DATABASE_URL environment variable."
        )
    return url
