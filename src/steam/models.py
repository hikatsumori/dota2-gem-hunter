from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GemType(str, Enum):
    """Enumeration of gem types for socketed items."""

    ETHEREAL = "ethereal"
    PRISMATIC = "prismatic"
    REGULAR = "regular"


class Item(BaseModel):
    """Model for items that can have sockets (couriers, wards, announcers, etc.)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    market_hash_name: str = Field(..., max_length=500)
    name: str = Field(..., max_length=200)
    item_type: str | None = Field(None, max_length=50)
    quality: str | None = Field(None, max_length=50)
    rarity: str | None = Field(None, max_length=50)
    can_have_sockets: bool = False
    max_sockets: int = 0
    created_at: datetime


class Listing(BaseModel):
    """Model for individual Steam Market listings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int | None = None
    listing_id: str | None = Field(None, max_length=100)
    price_usd: Decimal = Field(..., ge=0, max_digits=10, decimal_places=2)
    price_raw: int = Field(..., ge=0)
    currency: str = Field(default="USD", max_length=10)
    seller_steamid: str | None = Field(None, max_length=50)
    listing_url: str | None = None
    raw_description: str | None = None
    fetched_at: datetime
    expires_at: datetime | None = None


class Gem(BaseModel):
    """Model for gems found in socketed listings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    gem_type: GemType
    gem_name: str = Field(..., max_length=200)
    socket_index: int | None = None
    is_rare: bool = False
    created_at: datetime


class PriceHistory(BaseModel):
    """Model for aggregated price history snapshots."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int | None = None
    min_price_usd: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2
    )
    avg_price_usd: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2
    )
    max_price_usd: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2
    )
    listing_count: int | None = Field(None, ge=0)
    recorded_at: datetime


class AlertRule(BaseModel):
    """Model for alert configuration rules."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str = Field(..., max_length=100)
    gem_type: str | None = Field(None, max_length=20)
    gem_names: list[str] | None = None
    max_price_usd: Decimal | None = Field(
        None, ge=0, max_digits=10, decimal_places=2
    )
    min_discount_pct: Decimal | None = Field(
        None, ge=0, max_digits=5, decimal_places=2
    )
    is_active: bool = True
    created_at: datetime


class AlertSent(BaseModel):
    """Model for tracking sent alerts (deduplication)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int | None = None
    rule_id: int | None = None
    message_hash: str | None = Field(None, max_length=64)
    sent_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# API Response / Parser Output Models
# ──────────────────────────────────────────────────────────────────────────────

class MarketSearchResult(BaseModel):
    """Single result from Steam Market search API."""

    model_config = ConfigDict(extra="allow")

    name: str
    hash_name: str
    sell_price_text: str | None = None
    sell_price: int | None = None
    app_icon: str | None = None
    app_name: str | None = None
    asset_description: dict[str, Any] | None = None


class MarketSearchResponse(BaseModel):
    """Response from Steam Market search/render API."""

    model_config = ConfigDict(extra="allow")

    success: bool
    start: int
    pagesize: int
    total_count: int
    results: list[MarketSearchResult] = Field(default_factory=list)
    searchdata: dict[str, Any] | None = None


class GemParseData(BaseModel):
    """Parsed gem data from a listing page."""

    model_config = ConfigDict(extra="allow")

    gem_type: GemType
    gem_name: str
    socket_index: int | None = None
    is_rare: bool = False


class ListingPageData(BaseModel):
    """Parsed output from a Steam Market listing page."""

    model_config = ConfigDict(extra="allow")

    item_name: str
    price_usd: Decimal = Field(..., ge=0, max_digits=10, decimal_places=2)
    currency: str = Field(default="USD", max_length=10)
    description_html: str
    gems: list[GemParseData] = Field(default_factory=list)
    listing_url: str
    listing_id: str | None = Field(None, max_length=100)
    seller_steamid: str | None = Field(None, max_length=50)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
