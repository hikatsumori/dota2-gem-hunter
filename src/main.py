 
"""
Main scanner orchestration for Dota 2 Steam Market Rare Socket Scanner.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

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
from src.notifier.telegram import TelegramNotifier
from src.steam.client import create_steam_client
from src.steam.models import (
    Gem,
    Item,
    Listing,
    ListingPageData,
    PriceHistory,
)
from src.steam.parser import ListingParser

logger = logging.getLogger(__name__)


def is_steam_block_error(error: Exception) -> bool:
    """Detect Steam HTTP 403/429 errors, including wrapped HTTP errors."""

    current = error
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))

        status_code = getattr(current, "status_code", None)

        response = getattr(current, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code in (403, 429):
            return True

        message = str(current).lower()
        if "403 forbidden" in message or "429 too many requests" in message:
            return True

        current = current.__cause__ or current.__context__

    return False


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

        for error in self.errors[:5]:
            logger.warning("Scan error (%s): %s", self.item_type, error)


async def scan_item_type(item_type: str) -> ScanMetrics:
    """Scan Steam Market for a specific item type."""

    metrics = ScanMetrics(item_type)
    max_pages = SCAN_CONFIG["max_pages_per_type"]
    scan_time = datetime.now(UTC)

    pool = await get_pool()

    item_repo = ItemRepository(pool)
    listing_repo = ListingRepository(pool)
    gem_repo = GemRepository(pool)
    price_history_repo = PriceHistoryRepository(pool)
    alert_repo = AlertRepository(pool)

    parser = ListingParser()
    gem_detector = GemDetector()
    price_analyzer = PriceAnalyzer(price_history_repo)
    notifier = TelegramNotifier()

    try:
        async with create_steam_client() as steam_client:
            all_listings_for_price_history: dict[int, list[Listing]] = {}

            for page in range(max_pages):
                metrics.pages_scanned += 1

                try:
                    search_response = await steam_client.search_market(
                        item_type,
                        page,
                    )

                except Exception as e:
                    if is_steam_block_error(e):
                        logger.critical(
                            "Steam returned HTTP 403/429 during market search. "
                            "Stopping scanner immediately."
                        )
                        raise

                    error_msg = f"Page {page} search failed: {e}"
                    metrics.errors.append(error_msg)
                    logger.error(
                        "Search failed for %s page %d: %s",
                        item_type,
                        page,
                        e,
                    )
                    continue

                if not search_response.success or not search_response.results:
                    logger.info(
                        "No results for %s page %d, stopping pagination",
                        item_type,
                        page,
                    )
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
                        listing_url = (
                            f"{SCAN_CONFIG['listing_base_url']}"
                            f"{market_hash_name}"
                        )

                        try:
                            html = await steam_client.fetch_listing_page(
                                listing_url
                            )
                            page_data: ListingPageData = parser.parse_listing(
                                html,
                                listing_url,
                            )
                            metrics.listings_parsed += 1

                        except Exception as e:
                            if is_steam_block_error(e):
                                logger.critical(
                                    "Steam returned HTTP 403/429 while "
                                    "fetching an item page. Stopping scanner."
                                )
                                raise

                            metrics.listings_failed += 1
                            error_msg = (
                                f"Failed to fetch/parse listing page for "
                                f"{market_hash_name}: {e}"
                            )
                            metrics.errors.append(error_msg)
                            logger.warning(
                                "Parse failed for %s: %s",
                                market_hash_name,
                                e,
                            )

                            # Record search-result price if page parsing fails.
                            if result.sell_price is not None:
                                try:
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

                                    price_usd = Decimal(
                                        str(result.sell_price / 100)
                                    )

                                    listing = Listing(
                                        id=0,
                                        item_id=item_id,
                                        listing_id=None,
                                        price_usd=price_usd,
                                        price_raw=result.sell_price,
                                        currency="USD",
                                        seller_steamid=None,
                                        listing_url=listing_url,
                                        raw_description=None,
                                        fetched_at=scan_time,
                                    )

                                    all_listings_for_price_history.setdefault(
                                        item_id,
                                        [],
                                    ).append(listing)

                                except Exception as inner_e:
                                    logger.error(
                                        "Failed to record fallback price "
                                        "for %s: %s",
                                        market_hash_name,
                                        inner_e,
                                    )

                            continue

                        item = Item(
                            id=0,
                            market_hash_name=market_hash_name,
                            name=page_data.item_name,
                            item_type=item_type,
                            quality=None,
                            rarity=None,
                            can_have_sockets=len(page_data.gems) > 0,
                            max_sockets=(
                                max(
                                    (g.socket_index or 0)
                                    for g in page_data.gems
                                )
                                if page_data.gems
                                else 0
                            ),
                            created_at=scan_time,
                        )

                        item_id = await item_repo.upsert_item(item)
                        item.id = item_id

                        price_raw = int(page_data.price_usd * 100)

                        listing = Listing(
                            id=0,
                            item_id=item_id,
                            listing_id=page_data.listing_id,
                            price_usd=page_data.price_usd,
                            price_raw=price_raw,
                            currency=page_data.currency,
                            seller_steamid=page_data.seller_steamid,
                            listing_url=listing_url,
                            raw_description=page_data.description_html,
                            fetched_at=scan_time,
                        )

                        listing_db_id = await listing_repo.insert_listing(
                            listing
                        )
                        listing.id = listing_db_id

                        detected_gems = gem_detector.detect_gems(
                            page_data.description_html
                        )

                        final_gems: list[Gem] = []

                        for detected_gem in detected_gems:
                            detected_gem.listing_id = listing_db_id
                            final_gems.append(detected_gem)

                        detected_names = {
                            gem.gem_name.lower() for gem in final_gems
                        }

                        for parsed_gem in page_data.gems:
                            if (
                                parsed_gem.is_rare
                                and parsed_gem.gem_name.lower()
                                not in detected_names
                            ):
                                final_gems.append(
                                    Gem(
                                        id=0,
                                        listing_id=listing_db_id,
                                        gem_type=parsed_gem.gem_type,
                                        gem_name=parsed_gem.gem_name,
                                        socket_index=parsed_gem.socket_index,
                                        is_rare=parsed_gem.is_rare,
                                        created_at=scan_time,
                                    )
                                )

                        if final_gems:
                            await gem_repo.insert_gems(
                                listing_db_id,
                                final_gems,
                            )
                            metrics.gems_found += len(final_gems)
                            metrics.rare_gems_found += sum(
                                1 for gem in final_gems if gem.is_rare
                            )

                        all_listings_for_price_history.setdefault(
                            item_id,
                            [],
                        ).append(listing)

                        alert_matches = (
                            await price_analyzer.aanalyze_listing(
                                listing,
                                item,
                                final_gems,
                            )
                        )

                        for alert_match in alert_matches:
                            has_been_sent = (
                                await alert_repo.has_alert_been_sent(
                                    listing_db_id,
                                    alert_match.rule_name,
                                )
                            )

                            if has_been_sent:
                                metrics.alerts_deduplicated += 1
                                continue

                            try:
                                await notifier.send_alert(
                                    alert_match,
                                    listing,
                                    item,
                                    final_gems,
                                )

                                await alert_repo.record_alert_sent(
                                    listing_db_id,
                                    alert_match.rule_name,
                                )

                                metrics.alerts_sent += 1

                                logger.info(
                                    "Alert sent: item=%s, rule=%s, discount=%.2f%%",
                                    item.name,
                                    alert_match.rule_name,
                                    alert_match.discount_pct,
                                )

                            except Exception as e:
                                error_msg = (
                                    f"Failed to send alert for {item.name}: {e}"
                                )
                                metrics.errors.append(error_msg)
                                logger.error(
                                    "Alert send failed for %s: %s",
                                    item.name,
                                    e,
                                )

                    except Exception as e:
                        if is_steam_block_error(e):
                            logger.critical(
                                "Steam returned HTTP 403/429 while "
                                "processing a listing. Stopping scanner."
                            )
                            raise

                        metrics.listings_failed += 1
                        error_msg = (
                            f"Unexpected error processing listing "
                            f"{market_hash_name}: {e}"
                        )
                        metrics.errors.append(error_msg)
                        logger.exception(
                            "Error processing listing on %s page %d",
                            item_type,
                            page,
                        )

            # Record price history for items seen during this scan.
            for item_id, listings in all_listings_for_price_history.items():
                if not listings:
                    continue

                prices = [listing.price_usd for listing in listings]

                price_history = PriceHistory(
                    id=0,
                    item_id=item_id,
                    min_price_usd=min(prices),
                    avg_price_usd=(
                        sum(prices, Decimal("0"))
                        / Decimal(str(len(prices)))
                    ),
                    max_price_usd=max(prices),
                    listing_count=len(prices),
                    recorded_at=scan_time,
                )

                try:
                    await price_history_repo.record_price_history(
                        price_history
                    )
                except Exception as e:
                    metrics.errors.append(
                        f"Failed to record price history for item "
                        f"{item_id}: {e}"
                    )
                    logger.error(
                        "Price history recording failed for item %d: %s",
                        item_id,
                        e,
                    )

    finally:
        await notifier.close()

    logger.info("Completed scan for %s", item_type)
    metrics.log_summary()

    return metrics


async def main() -> None:
    """Run scans sequentially for all configured item types."""

    missing = validate_required_env()

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            f"{', '.join(missing)}"
        )

    item_types = SCAN_CONFIG["item_types"]

    logger.info(
        "Starting full market scan for item types: %s",
        item_types,
    )

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
        for index, item_type in enumerate(item_types):
            logger.info("=" * 50)
            logger.info("Starting scan for item type: %s", item_type)
            logger.info("=" * 50)

            # A Steam 403/429 exception propagates here and stops the run.
            metrics = await scan_item_type(item_type)

            overall_metrics["item_types_scanned"] += 1
            overall_metrics["total_listings_checked"] += (
                metrics.listings_checked
            )
            overall_metrics["total_gems_found"] += metrics.gems_found
            overall_metrics["total_rare_gems_found"] += (
                metrics.rare_gems_found
            )
            overall_metrics["total_alerts_sent"] += metrics.alerts_sent
            overall_metrics["total_errors"] += len(metrics.errors)

            logger.info("Completed item type: %s", item_type)

            if index < len(item_types) - 1:
                await asyncio.sleep(2)

    finally:
        scan_completed_at = datetime.now(UTC)
        duration_seconds = (
            scan_completed_at - scan_started_at
        ).total_seconds()

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    asyncio.run(main())
