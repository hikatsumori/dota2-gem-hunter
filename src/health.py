"""Health monitoring and metrics logging for scan runs.

This module provides functions to log scan metrics to the database
for monitoring and observability.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.database.connection import get_pool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def log_scan_metrics(
    scan_type: str,
    listings_scanned: int,
    gems_found: int,
    alerts_sent: int,
    errors: int,
    duration_seconds: float,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> int:
    """
    Insert a scan run summary into the scan_metrics table.

    Args:
        scan_type: Type of scan (e.g., 'full', 'cleanup', 'courier', 'ward', etc.)
        listings_scanned: Number of listings checked during the scan
        gems_found: Number of gems found
        alerts_sent: Number of alerts sent
        errors: Number of errors encountered
        duration_seconds: Duration of the scan in seconds
        started_at: Scan start timestamp (defaults to now - duration if not provided)
        completed_at: Scan completion timestamp (defaults to now if not provided)

    Returns:
        The inserted scan_metrics row ID.
    """
    pool = await get_pool()

    now = datetime.now(UTC)
    if completed_at is None:
        completed_at = now
    if started_at is None:
        started_at = now - timedelta(seconds=duration_seconds)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO scan_metrics (
                scan_type,
                listings_scanned,
                gems_found,
                alerts_sent,
                errors,
                duration_seconds,
                started_at,
                completed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            scan_type,
            listings_scanned,
            gems_found,
            alerts_sent,
            errors,
            duration_seconds,
            started_at,
            completed_at,
        )

    scan_id: int = row["id"]
    logger.info(
        "Logged scan metrics: id=%d, type=%s, listings=%d, gems=%d, alerts=%d, errors=%d, duration=%.2fs",
        scan_id,
        scan_type,
        listings_scanned,
        gems_found,
        alerts_sent,
        errors,
        duration_seconds,
    )

    return scan_id


async def get_recent_scan_metrics(hours: int = 24) -> list[dict]:
    """
    Retrieve recent scan metrics for monitoring dashboards.

    Args:
        hours: How many hours back to fetch (default 24)

    Returns:
        List of scan metrics records as dictionaries.
    """
    pool = await get_pool()
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM scan_metrics
            WHERE completed_at >= $1
            ORDER BY completed_at DESC
            """,
            since,
        )

    return [dict(row) for row in rows]


async def get_scan_metrics_summary(hours: int = 24) -> dict:
    """
    Get aggregated scan metrics summary for the last N hours.

    Returns:
        Dictionary with aggregated counts and averages.
    """
    pool = await get_pool()
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total_scans,
                SUM(listings_scanned) as total_listings,
                SUM(gems_found) as total_gems,
                SUM(alerts_sent) as total_alerts,
                SUM(errors) as total_errors,
                AVG(duration_seconds) as avg_duration,
                MAX(completed_at) as latest_scan
            FROM scan_metrics
            WHERE completed_at >= $1
            """,
            since,
        )

    if row:
        return dict(row)
    return {
        "total_scans": 0,
        "total_listings": 0,
        "total_gems": 0,
        "total_alerts": 0,
        "total_errors": 0,
        "avg_duration": 0,
        "latest_scan": None,
    }
