"""Cleanup job for removing old data from the database.

This module provides a standalone cleanup function that can be run
independently (e.g., via cron) to remove data older than the retention period.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.database.connection import close_pool, get_pool
from src.database.repositories import (
    AlertRepository,
    GemRepository,
    ListingRepository,
    PriceHistoryRepository,
)

logger = logging.getLogger(__name__)


async def cleanup_old_data(older_than_days: int = 30) -> dict[str, int]:
    """
    Remove old data from all repositories.

    Args:
        older_than_days: Retention period in days. Data older than this will be deleted.
                        Defaults to 30 days.

    Returns:
        Dictionary with counts of deleted rows per table.
    """
    logger.info("Starting cleanup of data older than %d days", older_than_days)

    pool = await get_pool()

    # Create repository instances
    listing_repo = ListingRepository(pool)
    price_history_repo = PriceHistoryRepository(pool)
    alert_repo = AlertRepository(pool)
    gem_repo = GemRepository(pool)

    results = {}

    try:
        # Cleanup listings (cascades to gems via FK if CASCADE is set, but we do gems separately for clarity)
        logger.info("Cleaning up old listings...")
        listings_deleted = await listing_repo.cleanup_old(older_than_days)
        results["listings_deleted"] = listings_deleted
        logger.info("Deleted %d old listings", listings_deleted)

        # Cleanup gems (by created_at)
        logger.info("Cleaning up old gems...")
        gems_deleted = await gem_repo.cleanup_old(older_than_days)
        results["gems_deleted"] = gems_deleted
        logger.info("Deleted %d old gems", gems_deleted)

        # Cleanup price history
        logger.info("Cleaning up old price history...")
        price_history_deleted = await price_history_repo.cleanup_old(older_than_days)
        results["price_history_deleted"] = price_history_deleted
        logger.info("Deleted %d old price history records", price_history_deleted)

        # Cleanup alerts_sent
        logger.info("Cleaning up old alert records...")
        alerts_deleted = await alert_repo.cleanup_old(older_than_days)
        results["alerts_deleted"] = alerts_deleted
        logger.info("Deleted %d old alert records", alerts_deleted)

        total_deleted = sum(results.values())
        logger.info("Cleanup complete. Total rows deleted: %d", total_deleted)

    finally:
        await close_pool()

    return results


async def cleanup_gems_orphaned() -> int:
    """
    Clean up orphaned gems (gems whose listing_id no longer exists).

    This is a safety net in case FK cascade didn't work or was disabled.

    Returns:
        Number of deleted orphaned gems.
    """
    logger.info("Checking for orphaned gems...")

    pool = await get_pool()
    gem_repo = GemRepository(pool)

    try:
        async with pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM gems
                WHERE listing_id IS NOT NULL
                AND listing_id NOT IN (SELECT id FROM listings)
            """)
            deleted = int(result.split()[-1]) if result != "DELETE 0" else 0
            if deleted:
                logger.info("Deleted %d orphaned gems", deleted)
            return deleted
    finally:
        await close_pool()


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    asyncio.run(cleanup_old_data())