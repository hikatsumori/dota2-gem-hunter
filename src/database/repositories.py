"""Database repository classes for data access layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from src.steam.models import AlertRule, AlertSent, Gem, GemType, Item, Listing, PriceHistory


class ItemRepository:
    """Repository for items that can have sockets."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_item(self, item: Item) -> int:
        """
        Insert or update an item by market_hash_name.
        Returns the item ID.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO items (market_hash_name, name, item_type, quality, rarity,
                                   can_have_sockets, max_sockets, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (market_hash_name) DO UPDATE SET
                    name = EXCLUDED.name,
                    item_type = EXCLUDED.item_type,
                    quality = EXCLUDED.quality,
                    rarity = EXCLUDED.rarity,
                    can_have_sockets = EXCLUDED.can_have_sockets,
                    max_sockets = EXCLUDED.max_sockets
                RETURNING id
                """,
                item.market_hash_name,
                item.name,
                item.item_type,
                item.quality,
                item.rarity,
                item.can_have_sockets,
                item.max_sockets,
                item.created_at,
            )
            return row["id"]  # type: ignore[no-any-return]

    async def get_by_market_hash(self, market_hash_name: str) -> Item | None:
        """Get an item by its market hash name."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM items WHERE market_hash_name = $1",
                market_hash_name,
            )
            if row:
                return Item(**dict(row))
            return None

    async def get_socketable_items(self) -> list[Item]:
        """Get all items that can have sockets."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM items WHERE can_have_sockets = TRUE ORDER BY name"
            )
            return [Item(**dict(row)) for row in rows]


class ListingRepository:
    """Repository for Steam Market listings."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_listing(self, listing: Listing) -> int:
        """
        Insert a new listing and return its ID.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO listings (item_id, listing_id, price_usd, price_raw, currency,
                                      seller_steamid, listing_url, raw_description, fetched_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                listing.item_id,
                listing.listing_id,
                listing.price_usd,
                listing.price_raw,
                listing.currency,
                listing.seller_steamid,
                listing.listing_url,
                listing.raw_description,
                listing.fetched_at,
                listing.expires_at,
            )
            return row["id"]  # type: ignore[no-any-return]

    async def get_recent_listings(self, item_id: int, hours: int = 24) -> list[Listing]:
        """Get listings for an item within the last N hours."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM listings
                WHERE item_id = $1 AND fetched_at >= $2
                ORDER BY fetched_at DESC
                """,
                item_id,
                since,
            )
            return [Listing(**dict(row)) for row in rows]

    async def cleanup_old(self, older_than_days: int = 30) -> int:
        """
        Delete listings older than N days.
        Returns the number of deleted rows.
        """
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM listings WHERE fetched_at < $1",
                cutoff,
            )
            # result is like "DELETE 42"
            return int(result.split()[-1]) if result != "DELETE 0" else 0


class GemRepository:
    """Repository for gems found in socketed listings."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_gems(self, listing_id: int, gems: list[Gem]) -> list[int]:
        """
        Batch insert gems for a listing.
        Returns list of inserted gem IDs.
        """
        if not gems:
            return []

        async with self._pool.acquire() as conn:
            # Use executemany for batch insert
            gem_ids = []
            for gem in gems:
                row = await conn.fetchrow(
                    """
                    INSERT INTO gems (listing_id, gem_type, gem_name, socket_index, is_rare, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    listing_id,
                    gem.gem_type.value if isinstance(gem.gem_type, GemType) else gem.gem_type,
                    gem.gem_name,
                    gem.socket_index,
                    gem.is_rare,
                    gem.created_at,
                )
                gem_ids.append(row["id"])
            return gem_ids

    async def get_gems_for_listing(self, listing_id: int) -> list[Gem]:
        """Get all gems for a specific listing."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM gems WHERE listing_id = $1 ORDER BY socket_index",
                listing_id,
            )
            return [Gem(**dict(row)) for row in rows]


class PriceHistoryRepository:
    """Repository for aggregated price history snapshots."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_price_history(self, price_history: PriceHistory) -> int:
        """
        Record a price history snapshot.
        Returns the inserted row ID.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO price_history (item_id, min_price_usd, avg_price_usd,
                                           max_price_usd, listing_count, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                price_history.item_id,
                price_history.min_price_usd,
                price_history.avg_price_usd,
                price_history.max_price_usd,
                price_history.listing_count,
                price_history.recorded_at,
            )
            return row["id"]  # type: ignore[no-any-return]

    async def get_average_price(self, item_id: int, days: int = 7) -> Decimal | None:
        """
        Calculate the average price over the last N days from price_history.
        Uses the avg_price_usd column weighted by listing_count.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    SUM(avg_price_usd * listing_count) / SUM(listing_count) AS weighted_avg
                FROM price_history
                WHERE item_id = $1
                  AND recorded_at >= $2
                  AND avg_price_usd IS NOT NULL
                  AND listing_count IS NOT NULL
                  AND listing_count > 0
                """,
                item_id,
                since,
            )
            if row and row["weighted_avg"] is not None:
                return Decimal(str(row["weighted_avg"]))
            return None

    async def cleanup_old(self, older_than_days: int = 30) -> int:
        """
        Delete price history records older than N days.
        Returns the number of deleted rows.
        """
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM price_history WHERE recorded_at < $1",
                cutoff,
            )
            return int(result.split()[-1]) if result != "DELETE 0" else 0


class AlertRepository:
    """Repository for alert deduplication tracking."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def has_alert_been_sent(self, listing_id: int, rule_name: str) -> bool:
        """
        Check if an alert has already been sent for this listing and rule.
        Uses rule_name to find the rule_id, then checks alerts_sent.
        """
        async with self._pool.acquire() as conn:
            # First get rule_id from rule_name
            rule_row = await conn.fetchrow(
                "SELECT id FROM alert_rules WHERE name = $1",
                rule_name,
            )
            if not rule_row:
                return False

            rule_id = rule_row["id"]
            alert_row = await conn.fetchrow(
                """
                SELECT 1 FROM alerts_sent
                WHERE listing_id = $1 AND rule_id = $2
                """,
                listing_id,
                rule_id,
            )
            return alert_row is not None

    async def record_alert_sent(
        self, listing_id: int, rule_name: str, message_hash: str | None = None
    ) -> int:
        """
        Record that an alert was sent.
        Returns the inserted alert ID.
        """
        async with self._pool.acquire() as conn:
            # Get rule_id from rule_name
            rule_row = await conn.fetchrow(
                "SELECT id FROM alert_rules WHERE name = $1",
                rule_name,
            )
            if not rule_row:
                raise ValueError(f"Alert rule '{rule_name}' not found in database")

            rule_id = rule_row["id"]
            row = await conn.fetchrow(
                """
                INSERT INTO alerts_sent (listing_id, rule_id, message_hash, sent_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                listing_id,
                rule_id,
                message_hash,
                datetime.now(UTC),
            )
            return row["id"]  # type: ignore[no-any-return]