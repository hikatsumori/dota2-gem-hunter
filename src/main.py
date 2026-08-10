"""
Main scanner orchestration for Dota 2 Steam Market Rare Socket Scanner.

This module provides the entry point for scanning Steam Market listings
for rare socketed items (Ethereal/Prismatic gems) and sending alerts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.analyzer.gem_detector import GemDetector
from src.analyzer.price_analyzer import PriceAnalyzer
from src.config import SCAN_CONFIG, validate_required_env
from src.database.connection import close_pool, get_pool
from src.database.repositories import (
    AlertRepository,
    GemRepository,
    ItemRepository,
    ListingRepository,
    PriceHistoryRepository,
)
from src.health import log_scan_metrics
from src.notifier.discord import DiscordNotifier
from src.steam.client import create_steam_client
from src.steam.models import (
    Gem,
    Item,
    Listing,
    ListingPageData,
    PriceHistory,
)
from src.steam.parser import ListingParser

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ScanMetrics:
    """Tracks metrics for a single item_type scan."""

    def __init__(self, item_type: str) -> None:
        self.item_type = item_type
        self.pages_scanned = 0
        self.listings_checked = 0
        self.listings_parsed = 0
        self.listings_failed = 0
        self.gems_found = 0
        self.rare_gems_found = 0
        self.alerts_sent = 0
        self.alerts_deduplicated = 0
        self.errors: list[str] = []

    def log_summary(self) -> None:
        """Log a summary of the scan metrics."""
        logger.info(
            "Scan complete for %s: pages=%d, listings_checked=%d, parsed=%d, failed=%d, "
            "gems_found=%d, rare_gems=%d, alerts_sent=%d, alerts_dedup=%d, errors=%d",
            self.item_type,
            self.pages_scanned,
            self.listings_checked,
            self.listings_parsed,
            self.listings_failed,
            self.gems_found,
            self.rare_gems_found,
            self.alerts_sent,
            self.alerts_deduplicated,
            len(self.errors),
        )
        if self.errors:
            for err in self.errors[:5]:  # Log first 5 errors
                logger.warning("Scan error (%s): %s", self.item_type, err)


async def scan_item_type(item_type: str) -> ScanMetrics:
    """
    Scan Steam Market for a specific item type.

    Performs the full pipeline:
    1. Search market (max pages from SCAN_CONFIG)
    2. For each listing: fetch page HTML, parse, detect gems
    3. Upsert item, insert listing + gems
    4. Record price history (aggregated per item per scan)
    5. Analyze for alerts
    6. Send alerts (deduplicated)

    Args:
        item_type: Type of item to scan (courier, ward, announcer, hud, loading_screen)

    Returns:
        ScanMetrics with summary statistics
    """
    metrics = ScanMetrics(item_type)
    max_pages = SCAN_CONFIG["max_pages_per_type"]
    scan_time = datetime.now(UTC)

    # Get database pool and create repositories
    pool = await get_pool()
    item_repo = ItemRepository(pool)
    listing_repo = ListingRepository(pool)
    gem_repo = GemRepository(pool)
    price_history_repo = PriceHistoryRepository(pool)
    alert_repo = AlertRepository(pool)

    # Create shared components
    parser = ListingParser()
    gem_detector = GemDetector()
    price_analyzer = PriceAnalyzer(price_history_repo)
    notifier = DiscordNotifier()

    try:
        async with create_steam_client() as steam_client:
            # Collect listings per item for price history aggregation
            all_listings_for_price_history: dict[int, list[Listing]] = {}

            for page in range(max_pages):
                metrics.pages_scanned += 1

                try:
                    search_response = await steam_client.search_market(item_type, page)
                except Exception as e:
                    error_msg = f"Page {page} search failed: {e}"
                    metrics.errors.append(error_msg)
                    logger.error("Search failed for %s page %d: %s", item_type, page, e)
                    continue

                if not search_response.success or not search_response.results:
                    logger.info("No results for %s page %d, stopping pagination", item_type, page)
                    break

                logger.info(
                    "Processing %d results for %s page %d (total: %d)",
                    len(search_response.results),
                    item_type,
                    page,
                    search_response.total_count,
                )

                for result in search_response.results:
                    metrics.listings_checked += 1
                    market_hash_name = result.hash_name

                    try:
                        # Construct item listing page URL (not individual listing)
                        # The search results give us market_hash_name; we fetch the item page
                        listing_url = f"{SCAN_CONFIG['listing_base_url']}{market_hash_name}"

                        # Fetch and parse listing page
                        try:
                            html = await steam_client.fetch_listing_page(listing_url)
                            page_data: ListingPageData = parser.parse_listing(html, listing_url)
                            metrics.listings_parsed += 1
                        except Exception as e:
                            metrics.listings_failed += 1
                            error_msg = f"Failed to fetch/parse listing page for {market_hash_name}: {e}"
                            metrics.errors.append(error_msg)
                            logger.warning("Parse failed for %s: %s", market_hash_name, e)

                            # Graceful degradation: record price from search results if available
                            if result.sell_price is not None:
                                try:
                                    # We still need to upsert the item to record price history
                                    item = Item(
                                        id=0,
                                        market_hash_name=market_hash_name,
                                        name=result.name,
                                        item_type=item_type,
                                        quality=None,
                                        rarity=None,
                                        created_at=scan_time,
                                    )
                                    item_id = await item_repo.upsert_item(item)
                                    item.id = item_id

                                    price_usd = Decimal(str(result.sell_price / 100))
                                    price_raw = result.sell_price
                                    currency = "USD"  # Search API returns prices in cents USD

                                    # Create a minimal listing for price history
                                    listing = Listing(
                                        id=0,
                                        item_id=item_id,
                                        listing_id=None,
                                        price_usd=price_usd,
                                        price_raw=price_raw,
                                        currency=currency,
                                        seller_steamid=None,
                                        listing_url=listing_url,
                                        raw_description=None,
                                        fetched_at=scan_time,
                                    )

                                    if item_id not in all_listings_for_price_history:
                                        all_listings_for_price_history[item_id] = []
                                    all_listings_for_price_history[item_id].append(listing)

                                    logger.debug(
                                        "Recorded price for %s from search results: $%.2f (page fetch failed)",
                                        market_hash_name,
                                        price_usd,
                                    )
                                except Exception as inner_e:
                                    logger.error(
                                        "Failed to record price from search results for %s: %s",
                                        market_hash_name,
                                        inner_e,
                                    )
                            continue

                        # Extract data from parsed page
                        item_name = page_data.item_name
                        price_usd = page_data.price_usd
                        currency = page_data.currency
                        description_html = page_data.description_html
                        parsed_gems = page_data.gems
                        listing_id_from_page = page_data.listing_id
                        seller_steamid = page_data.seller_steamid

                        # Upsert Item
                        item = Item(
                            id=0,
                            market_hash_name=market_hash_name,
                            name=item_name,
                            item_type=item_type,
                            quality=None,
                            rarity=None,
                            can_have_sockets=len(parsed_gems) > 0,
                            max_sockets=max((g.socket_index or 0) for g in parsed_gems) if parsed_gems else 0,
                            created_at=scan_time,
                        )
                        item_id = await item_repo.upsert_item(item)
                        item.id = item_id

                        # Create Listing model
                        # Convert price to raw (cents)
                        price_raw = int(price_usd * 100)

                        listing = Listing(
                            id=0,
                            item_id=item_id,
                            listing_id=listing_id_from_page,
                            price_usd=price_usd,
                            price_raw=price_raw,
                            currency=currency,
                            seller_steamid=seller_steamid,
                            listing_url=listing_url,
                            raw_description=description_html,
                            fetched_at=scan_time,
                        )
                        listing_db_id = await listing_repo.insert_listing(listing)
                        listing.id = listing_db_id

                        # Detect gems using GemDetector (more thorough than parser)
                        detected_gems = gem_detector.detect_gems(description_html)

                        # Merge parser gems with detector gems (detector is source of truth for type matching)
                        # But keep socket_index from parser where available
                        final_gems: list[Gem] = []
                        for dgem in detected_gems:
                            dgem.listing_id = listing_db_id
                            final_gems.append(dgem)

                        # Also add any parser-only rare gems that detector missed
                        for pgem in parsed_gems:
                            if pgem.is_rare and pgem.gem_name.lower() not in [g.gem_name.lower() for g in final_gems]:
                                gem = Gem(
                                    id=0,
                                    listing_id=listing_db_id,
                                    gem_type=pgem.gem_type,
                                    gem_name=pgem.gem_name,
                                    socket_index=pgem.socket_index,
                                    is_rare=pgem.is_rare,
                                    created_at=scan_time,
                                )
                                final_gems.append(gem)

                        if final_gems:
                            await gem_repo.insert_gems(listing_db_id, final_gems)
                            metrics.gems_found += len(final_gems)
                            metrics.rare_gems_found += sum(1 for g in final_gems if g.is_rare)

                        # Collect for price history recording (per item)
                        if item_id not in all_listings_for_price_history:
                            all_listings_for_price_history[item_id] = []
                        all_listings_for_price_history[item_id].append(listing)

                        # Analyze for alerts
                        alert_matches = await price_analyzer.aanalyze_listing(listing, item, final_gems)

                        for alert_match in alert_matches:
                            # Check deduplication using database listing_id
                            has_been_sent = await alert_repo.has_alert_been_sent(listing_db_id, alert_match.rule_name)

                            if has_been_sent:
                                metrics.alerts_deduplicated += 1
                                logger.debug(
                                    "Alert deduplicated: listing_db_id=%d, rule=%s",
                                    listing_db_id,
                                    alert_match.rule_name,
                                )
                                continue

                            # Send alert
                            try:
                                await notifier.send_alert(alert_match, listing, item, final_gems)
                                # Record alert sent
                                await alert_repo.record_alert_sent(listing_db_id, alert_match.rule_name)
                                metrics.alerts_sent += 1
                                logger.info(
                                    "Alert sent: item=%s, rule=%s, discount=%.2f%%",
                                    item.name,
                                    alert_match.rule_name,
                                    alert_match.discount_pct,
                                )
                            except Exception as e:
                                error_msg = f"Failed to send alert for {item.name}: {e}"
                                metrics.errors.append(error_msg)
                                logger.error("Alert send failed for %s: %s", item.name, e)

                    except Exception as e:
                        metrics.listings_failed += 1
                        error_msg = f"Unexpected error processing listing {market_hash_name}: {e}"
                        metrics.errors.append(error_msg)
                        logger.exception("Error processing listing on %s page %d", item_type, page)

            # Record price history for each item seen in this scan
            for item_id, listings in all_listings_for_price_history.items():
                if not listings:
                    continue

                prices = [listing.price_usd for listing in listings]
                min_price = min(prices)
                max_price = max(prices)
                avg_price = sum(prices, Decimal("0")) / Decimal(str(len(prices)))
                count = len(prices)

                price_history = PriceHistory(
                    id=0,
                    item_id=item_id,
                    min_price_usd=min_price,
                    avg_price_usd=avg_price,
                    max_price_usd=max_price,
                    listing_count=count,
                    recorded_at=scan_time,
                )
                try:
                    await price_history_repo.record_price_history(price_history)
                except Exception as e:
                    metrics.errors.append(f"Failed to record price history for item {item_id}: {e}")
                    logger.error("Price history recording failed for item %d: %s", item_id, e)

    finally:
        await notifier.close()

    logger.info("Completed scan for %s", item_type)
    metrics.log_summary()
    return metrics


async def main() -> None:
    """
    Main entry point - runs scan for all configured item types.

    Iterates through SCAN_CONFIG.item_types sequentially,
    respecting rate limits via the SteamWebClient.
    """
    # Validate required environment variables
    missing = validate_required_env()
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    item_types = SCAN_CONFIG["item_types"]
    logger.info("Starting full market scan for item types: %s", item_types)

    scan_started_at = datetime.now(UTC)
    overall_metrics = {
        "item_types_scanned": 0,
        "total_listings_checked": 0,
        "total_gems_found": 0,
        "total_rare_gems_found": 0,
        "total_alerts_sent": 0,
        "total_errors": 0,
    }

    try:
        for item_type in item_types:
            logger.info("=" * 50)
            logger.info("Starting scan for item type: %s", item_type)
            logger.info("=" * 50)

            metrics = await scan_item_type(item_type)

            overall_metrics["item_types_scanned"] += 1
            overall_metrics["total_listings_checked"] += metrics.listings_checked
            overall_metrics["total_gems_found"] += metrics.gems_found
            overall_metrics["total_rare_gems_found"] += metrics.rare_gems_found
            overall_metrics["total_alerts_sent"] += metrics.alerts_sent
            overall_metrics["total_errors"] += len(metrics.errors)

            logger.info("Completed item type: %s", item_type)

            # Small delay between item types to be gentle on the API
            if item_type != item_types[-1]:  # Not the last one
                await asyncio.sleep(2)

    finally:
        scan_completed_at = datetime.now(UTC)
        duration_seconds = (scan_completed_at - scan_started_at).total_seconds()

        logger.info("=" * 50)
        logger.info("FULL SCAN COMPLETE")
        logger.info("=" * 50)
        logger.info(
            "Summary: item_types=%d, listings_checked=%d, gems_found=%d, "
            "rare_gems=%d, alerts_sent=%d, errors=%d",
            overall_metrics["item_types_scanned"],
            overall_metrics["total_listings_checked"],
            overall_metrics["total_gems_found"],
            overall_metrics["total_rare_gems_found"],
            overall_metrics["total_alerts_sent"],
            overall_metrics["total_errors"],
        )

        # Log scan metrics to database for health monitoring
        try:
            await log_scan_metrics(
                scan_type="full",
                listings_scanned=overall_metrics["total_listings_checked"],
                gems_found=overall_metrics["total_gems_found"],
                alerts_sent=overall_metrics["total_alerts_sent"],
                errors=overall_metrics["total_errors"],
                duration_seconds=duration_seconds,
                started_at=scan_started_at,
                completed_at=scan_completed_at,
            )
        except Exception as e:
            logger.error("Failed to log scan metrics: %s", e)

        await close_pool()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(main())
