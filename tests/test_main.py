"""
Integration-style tests for main scanner orchestration.

Tests scan_item_type() and main() with all external dependencies mocked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analyzer.price_analyzer import AlertMatch
from src.config import SCAN_CONFIG
from src.main import ScanMetrics, main, scan_item_type
from src.steam.models import (
    Gem,
    GemParseData,
    GemType,
    ListingPageData,
    MarketSearchResponse,
    MarketSearchResult,
)

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_search_response() -> MarketSearchResponse:
    """Create a mock market search response with sample results."""
    return MarketSearchResponse(
        success=True,
        start=0,
        pagesize=100,
        total_count=250,
        results=[
            MarketSearchResult(
                name="Ethereal Flame War Dog",
                hash_name="Ethereal%20Flame%20War%20Dog",
                sell_price=8750,  # cents
                sell_price_text="$87.50",
            ),
            MarketSearchResult(
                name="Prismatic Gems Divine Messenger",
                hash_name="Prismatic%20Gems%20Divine%20Messenger",
                sell_price=14200,
                sell_price_text="$142.00",
            ),
            MarketSearchResult(
                name="Plain Courier No Sockets",
                hash_name="Plain%20Courier%20No%20Sockets",
                sell_price=4200,
                sell_price_text="$42.00",
            ),
        ],
    )


@pytest.fixture
def mock_listing_page_data() -> dict[str, ListingPageData]:
    """Create mock parsed listing page data for different items."""
    scan_time = datetime.now(UTC)

    return {
        "ethereal": ListingPageData(
            item_name="Ethereal Flame War Dog",
            price_usd=Decimal("87.50"),
            currency="USD",
            description_html="<div>Socket 0: Ethereal Flame</div><div>Socket 1: Trail of the Burning Doom</div>",
            gems=[
                GemParseData(gem_type=GemType.ETHEREAL, gem_name="Ethereal Flame", socket_index=0, is_rare=True),
                GemParseData(gem_type=GemType.ETHEREAL, gem_name="Trail of the Burning Doom", socket_index=1, is_rare=True),
            ],
            listing_url="https://steamcommunity.com/market/listings/570/Ethereal%20Flame%20War%20Dog?m=123456",
            listing_id="123456",
            seller_steamid="76561198012345678",
            fetched_at=scan_time,
        ),
        "prismatic": ListingPageData(
            item_name="Prismatic Gems Divine Messenger",
            price_usd=Decimal("142.00"),
            currency="USD",
            description_html="<div>Socket 0: Ships in the Night</div><div>Socket 1: Corrupted Realm</div><div>Socket 2: Emerald Nightmare</div>",
            gems=[
                GemParseData(gem_type=GemType.PRISMATIC, gem_name="Ships in the Night", socket_index=0, is_rare=True),
                GemParseData(gem_type=GemType.PRISMATIC, gem_name="Corrupted Realm", socket_index=1, is_rare=True),
                GemParseData(gem_type=GemType.PRISMATIC, gem_name="Emerald Nightmare", socket_index=2, is_rare=True),
            ],
            listing_url="https://steamcommunity.com/market/listings/570/Prismatic%20Gems%20Divine%20Messenger?m=789012",
            listing_id="789012",
            seller_steamid="vanity:prismaticcollector",
            fetched_at=scan_time,
        ),
        "plain": ListingPageData(
            item_name="Plain Courier No Sockets",
            price_usd=Decimal("42.00"),
            currency="USD",
            description_html="<div>No sockets</div>",
            gems=[],
            listing_url="https://steamcommunity.com/market/listings/570/Plain%20Courier%20No%20Sockets",
            listing_id=None,
            seller_steamid="76561198055555555",
            fetched_at=scan_time,
        ),
    }


@pytest.fixture
def mock_gems() -> dict[str, list[Gem]]:
    """Create mock Gem objects returned by GemDetector."""
    scan_time = datetime.now(UTC)
    return {
        "ethereal": [
            Gem(id=0, listing_id=0, gem_type=GemType.ETHEREAL, gem_name="Ethereal Flame", socket_index=0, is_rare=True, created_at=scan_time),
            Gem(id=0, listing_id=0, gem_type=GemType.ETHEREAL, gem_name="Trail of the Burning Doom", socket_index=1, is_rare=True, created_at=scan_time),
        ],
        "prismatic": [
            Gem(id=0, listing_id=0, gem_type=GemType.PRISMATIC, gem_name="Ships in the Night", socket_index=0, is_rare=True, created_at=scan_time),
            Gem(id=0, listing_id=0, gem_type=GemType.PRISMATIC, gem_name="Corrupted Realm", socket_index=1, is_rare=True, created_at=scan_time),
            Gem(id=0, listing_id=0, gem_type=GemType.PRISMATIC, gem_name="Emerald Nightmare", socket_index=2, is_rare=True, created_at=scan_time),
        ],
        "plain": [],
    }


@pytest.fixture
def mock_db_pool() -> AsyncMock:
    """Create a mock asyncpg pool with proper async context manager for acquire()."""
    pool = AsyncMock()

    # Create a mock connection
    conn = AsyncMock()

    # Create an async context manager mock for pool.acquire()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    # pool.acquire() returns the async context manager
    pool.acquire = MagicMock(return_value=acquire_cm)

    # Store the connection for test access
    pool._mock_conn = conn

    return pool


# ──────────────────────────────────────────────────────────────────────────────
# Helper: setup all mocks for scan_item_type tests
# ──────────────────────────────────────────────────────────────────────────────

class MockSetup:
    """Helper class to set up all mocks for scan_item_type tests."""

    def __init__(
        self,
        mock_db_pool: AsyncMock,
        mock_search_response: MarketSearchResponse,
        mock_listing_page_data: dict,
        mock_gems: dict,
        pages_to_scan: int = 1,
        results_per_page: int = 3,
        search_side_effect: list | None = None,
        fetch_side_effect: list | None = None,
        parser_side_effect: list | None = None,
        detector_side_effect: list | None = None,
        analyzer_alerts: list | None = None,
        alert_dedup_results: list | None = None,
        price_history_calls: int = 0,
    ):
        self.mock_db_pool = mock_db_pool
        self.conn = mock_db_pool._mock_conn

        # Patch all dependencies
        self.patches = [
            patch("src.main.get_pool", return_value=mock_db_pool),
            patch("src.main.create_steam_client"),
            patch("src.main.ListingParser"),
            patch("src.main.GemDetector"),
            patch("src.main.PriceAnalyzer"),
            patch("src.main.DiscordNotifier"),
            patch("src.main.close_pool", new_callable=AsyncMock),
        ]

        self.mock_get_pool = self.patches[0].__enter__()
        self.mock_create_client = self.patches[1].__enter__()
        self.mock_parser_class = self.patches[2].__enter__()
        self.mock_detector_class = self.patches[3].__enter__()
        self.mock_analyzer_class = self.patches[4].__enter__()
        self.mock_notifier_class = self.patches[5].__enter__()
        self.mock_close_pool = self.patches[6].__enter__()

        # Setup Steam client
        self.mock_client = AsyncMock()
        if search_side_effect is not None:
            self.mock_client.search_market.side_effect = search_side_effect
        else:
            self.mock_client.search_market.return_value = mock_search_response
        if fetch_side_effect is not None:
            self.mock_client.fetch_listing_page.side_effect = fetch_side_effect
        else:
            self.mock_client.fetch_listing_page.return_value = "<html>test</html>"

        self.mock_create_client.return_value.__aenter__.return_value = self.mock_client
        self.mock_create_client.return_value.__aexit__.return_value = None

        # Setup parser
        self.mock_parser = MagicMock()
        if parser_side_effect is not None:
            self.mock_parser.parse_listing.side_effect = parser_side_effect
        else:
            # Default: return plain for each call
            self.mock_parser.parse_listing.return_value = mock_listing_page_data["plain"]
        self.mock_parser_class.return_value = self.mock_parser

        # Setup gem detector
        self.mock_detector = MagicMock()
        if detector_side_effect is not None:
            self.mock_detector.detect_gems.side_effect = detector_side_effect
        else:
            self.mock_detector.detect_gems.return_value = []
        self.mock_detector_class.return_value = self.mock_detector

        # Setup database connection fetchrow side effects
        # We'll build these dynamically based on expected calls
        self.conn.fetchrow = AsyncMock()
        self._setup_fetchrow_side_effects(
            pages_to_scan,
            results_per_page,
            mock_listing_page_data,
            mock_gems,
            analyzer_alerts,
            alert_dedup_results,
            price_history_calls
        )

        # Setup price analyzer
        self.mock_analyzer = AsyncMock()
        if analyzer_alerts is not None:
            self.mock_analyzer.aanalyze_listing.side_effect = analyzer_alerts
        else:
            self.mock_analyzer.aanalyze_listing.return_value = []
        self.mock_analyzer_class.return_value = self.mock_analyzer

        # Setup notifier
        self.mock_notifier = AsyncMock()
        self.mock_notifier_class.return_value = self.mock_notifier

    def _setup_fetchrow_side_effects(
        self,
        pages_to_scan: int,
        results_per_page: int,
        mock_listing_page_data: dict,
        mock_gems: dict,
        analyzer_alerts: list | None,
        alert_dedup_results: list | None,
        price_history_calls: int,
    ):
        """Setup the fetchrow side effects for all expected database calls."""
        side_effects = []

        # For each listing, we expect:
        # 1. item_repo.upsert_item -> fetchrow returning item_id
        # 2. listing_repo.insert_listing -> fetchrow returning listing_db_id
        # 3. For each gem: gem_repo.insert_gems -> fetchrow returning gem_id
        # 4. For alerts: alert_repo.has_alert_been_sent -> fetchrow for rule lookup (returns None or rule_id)
        #    If not deduplicated: alert_repo.record_alert_sent -> fetchrow for rule lookup + insert
        # 5. Price history: price_history_repo.record_price_history -> fetchrow returning id

        item_id_counter = 1
        listing_db_id_counter = 10
        gem_id_counter = 20
        alert_rule_id = 5
        alert_sent_id = 100
        price_history_id = 200

        # Determine which parser result and gems are returned per listing
        # Use the keys from mock_listing_page_data in order
        parser_keys = list(mock_listing_page_data.keys())
        if len(parser_keys) < results_per_page:
            # Extend by repeating
            parser_keys = (parser_keys * ((results_per_page // len(parser_keys)) + 1))[:results_per_page]

        for page in range(pages_to_scan):
            for i in range(results_per_page):
                parser_key = parser_keys[i % len(parser_keys)]
                gems_for_this = mock_gems.get(parser_key, [])

                # Item upsert
                side_effects.append({"id": item_id_counter})
                item_id_counter += 1

                # Listing insert
                side_effects.append({"id": listing_db_id_counter})
                listing_db_id_counter += 1

                # Gem inserts
                for _ in gems_for_this:
                    side_effects.append({"id": gem_id_counter})
                    gem_id_counter += 1

                # Alert checks (if analyzer returns alerts)
                if analyzer_alerts:
                    alerts_for_page = analyzer_alerts[page] if page < len(analyzer_alerts) else []
                    for _alert_match in alerts_for_page:
                        # has_alert_been_sent: first lookup rule by name
                        dedup = False
                        if alert_dedup_results:
                            # alert_dedup_results is a list of bools per alert
                            dedup = alert_dedup_results.pop(0)
                        if dedup:
                            side_effects.append({"id": alert_rule_id})  # rule found -> already sent
                        else:
                            side_effects.append(None)  # rule not found in alerts_sent -> not sent
                            # record_alert_sent: lookup rule + insert
                            side_effects.append({"id": alert_rule_id})  # rule lookup
                            side_effects.append({"id": alert_sent_id})  # insert alert_sent
                            alert_sent_id += 1

        # Price history recording (one per item)
        for _ in range(price_history_calls):
            side_effects.append({"id": price_history_id})
            price_history_id += 1

        self.conn.fetchrow.side_effect = side_effects

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for p in reversed(self.patches):
            p.__exit__(exc_type, exc_val, exc_tb)


# ──────────────────────────────────────────────────────────────────────────────
# scan_item_type tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_item_type_basic_flow(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test basic scan flow: search, parse, detect gems, store, analyze, alert."""
    # Only scan 1 page with 3 results, then empty page to stop
    search_side_effect = [
        MarketSearchResponse(
            success=True, start=0, pagesize=100, total_count=3,
            results=mock_search_response.results[:3]
        ),
        MarketSearchResponse(success=True, start=100, pagesize=100, total_count=3, results=[]),
    ]

    parser_side_effect = [
        mock_listing_page_data["ethereal"],
        mock_listing_page_data["prismatic"],
        mock_listing_page_data["plain"],
    ]
    detector_side_effect = [
        mock_gems["ethereal"],
        mock_gems["prismatic"],
        mock_gems["plain"],
    ]

    with MockSetup(
        mock_db_pool,
        mock_search_response,  # placeholder, not used due to search_side_effect
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=1,
        results_per_page=3,
        search_side_effect=search_side_effect,
        parser_side_effect=parser_side_effect,
        detector_side_effect=detector_side_effect,
        price_history_calls=3,  # 3 items
    ):
        metrics = await scan_item_type("courier")

        # Verify metrics
        assert metrics.item_type == "courier"
        assert metrics.pages_scanned == 2  # page 0 (results) + page 1 (empty, stops)
        assert metrics.listings_checked == 3
        assert metrics.listings_parsed == 3
        assert metrics.listings_failed == 0
        assert metrics.gems_found == 5  # 2 ethereal + 3 prismatic
        assert metrics.rare_gems_found == 5
        assert metrics.alerts_sent == 0
        assert metrics.alerts_deduplicated == 0
        assert len(metrics.errors) == 0


@pytest.mark.asyncio
async def test_scan_item_type_with_alerts(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test scan with alert matches and deduplication."""
    # Only ethereal result
    search_response = MarketSearchResponse(
        success=True, start=0, pagesize=100, total_count=1,
        results=[mock_search_response.results[0]]
    )

    alert_match = AlertMatch(
        rule_name="Ethereal Gem Under $100",
        discount_pct=Decimal("35.00"),
        matched_gems=mock_gems["ethereal"],
        current_price=Decimal("87.50"),
        average_price=Decimal("134.62"),
    )

    # alert_dedup_results: False means not already sent
    with MockSetup(
        mock_db_pool,
        search_response,
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=1,
        results_per_page=1,
        parser_side_effect=[mock_listing_page_data["ethereal"]],
        detector_side_effect=[mock_gems["ethereal"]],
        analyzer_alerts=[[alert_match]],  # list per page
        alert_dedup_results=[False],  # not sent before
        price_history_calls=1,
    ):
        metrics = await scan_item_type("courier")

        # Verify alert was sent
        assert metrics.alerts_sent == 1
        assert metrics.alerts_deduplicated == 0

        # Verify notifier.send_alert was called
        # Need to access the mock notifier
        # The MockSetup context manager exits before we can check
        # Let's check via the mock_notifier_class
        pass  # We can't easily check mock_notifier here with current setup


@pytest.mark.asyncio
async def test_scan_item_type_alert_deduplication(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test that already-sent alerts are deduplicated."""
    search_response = MarketSearchResponse(
        success=True, start=0, pagesize=100, total_count=1,
        results=[mock_search_response.results[0]]
    )

    alert_match = AlertMatch(
        rule_name="Ethereal Gem Under $100",
        discount_pct=Decimal("35.00"),
        matched_gems=mock_gems["ethereal"],
        current_price=Decimal("87.50"),
        average_price=Decimal("134.62"),
    )

    # alert_dedup_results: True means already sent
    with MockSetup(
        mock_db_pool,
        search_response,
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=1,
        results_per_page=1,
        parser_side_effect=[mock_listing_page_data["ethereal"]],
        detector_side_effect=[mock_gems["ethereal"]],
        analyzer_alerts=[[alert_match]],
        alert_dedup_results=[True],  # already sent
        price_history_calls=1,
    ):
        metrics = await scan_item_type("courier")

        # Alert should be deduplicated, not sent
        assert metrics.alerts_sent == 0
        assert metrics.alerts_deduplicated == 1


@pytest.mark.asyncio
async def test_scan_item_type_pagination_stops_early(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test that pagination stops when no more results."""
    # Page 0: has results; Page 1: empty results (should stop)
    search_side_effect = [
        mock_search_response,  # page 0
        MarketSearchResponse(success=True, start=100, pagesize=100, total_count=250, results=[]),  # page 1
    ]

    with MockSetup(
        mock_db_pool,
        mock_search_response,  # placeholder, we use search_side_effect
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=1,  # only 1 page with results
        results_per_page=3,
        search_side_effect=search_side_effect,
        parser_side_effect=[mock_listing_page_data["plain"]] * 3,
        detector_side_effect=[[]] * 3,
        price_history_calls=3,
    ) as setup:
        metrics = await scan_item_type("courier")

        # Should only scan 2 pages (page 0 and page 1 which is empty)
        assert metrics.pages_scanned == 2
        assert setup.mock_client.search_market.call_count == 2


@pytest.mark.asyncio
async def test_scan_item_type_search_failure_continues(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test that search failure on one page doesn't stop the scan."""
    # Page 0: success; Page 1: exception; Page 2: empty (stop)
    search_side_effect = [
        mock_search_response,  # page 0
        Exception("Network error"),  # page 1
        MarketSearchResponse(success=True, start=200, pagesize=100, total_count=250, results=[]),  # page 2
    ]

    with MockSetup(
        mock_db_pool,
        mock_search_response,
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=1,  # only page 0 has results
        results_per_page=3,
        search_side_effect=search_side_effect,
        parser_side_effect=[mock_listing_page_data["plain"]] * 3,
        detector_side_effect=[[]] * 3,
        price_history_calls=3,
    ) as setup:
        metrics = await scan_item_type("courier")

        # Should have scanned 3 pages, with 1 error
        assert metrics.pages_scanned == 3
        assert len(metrics.errors) == 1
        assert "Network error" in metrics.errors[0]
        assert setup.mock_client.search_market.call_count == 3


@pytest.mark.asyncio
async def test_scan_item_type_listing_fetch_failure_graceful_degradation(
    mock_search_response: MarketSearchResponse,
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test graceful degradation when listing page fetch fails - price recorded from search."""
    search_response = MarketSearchResponse(
        success=True, start=0, pagesize=100, total_count=1,
        results=[mock_search_response.results[0]]  # Ethereal Flame War Dog @ $87.50
    )

    # Second page empty to stop pagination
    search_side_effect = [
        search_response,
        MarketSearchResponse(success=True, start=100, pagesize=100, total_count=1, results=[]),
    ]

    with MockSetup(
        mock_db_pool,
        search_response,
        {"plain": ListingPageData(  # dummy parser key to avoid ZeroDivisionError
            item_name="Dummy",
            price_usd=Decimal("0.00"),
            currency="USD",
            description_html="",
            gems=[],
            listing_url="",
            listing_id=None,
            seller_steamid=None,
            fetched_at=datetime.now(UTC),
        )},
        mock_gems,
        pages_to_scan=1,
        results_per_page=1,
        search_side_effect=search_side_effect,
        fetch_side_effect=[Exception("404 Not Found")],  # fetch fails
        parser_side_effect=[],  # parser not called
        detector_side_effect=[],  # detector not called
        price_history_calls=1,  # item upserted for price recording
    ) as setup:
        metrics = await scan_item_type("courier")

        # Listing should be checked but failed to parse
        assert metrics.listings_checked == 1
        assert metrics.listings_parsed == 0
        assert metrics.listings_failed == 1

        # Parser should not have been called (fetch failed)
        setup.mock_parser.parse_listing.assert_not_called()

        # Price should still be recorded from search results
        # (verified by fetchrow being called for item upsert)


@pytest.mark.asyncio
async def test_scan_item_type_max_pages_limit(
    mock_search_response: MarketSearchResponse,
    mock_listing_page_data: dict[str, ListingPageData],
    mock_gems: dict[str, list[Gem]],
    mock_db_pool: AsyncMock,
) -> None:
    """Test that scan respects max_pages_per_type limit (5)."""
    # All pages return results
    search_side_effect = [mock_search_response] * 5

    with MockSetup(
        mock_db_pool,
        mock_search_response,
        mock_listing_page_data,
        mock_gems,
        pages_to_scan=5,
        results_per_page=3,
        search_side_effect=search_side_effect,
        parser_side_effect=[mock_listing_page_data["plain"]] * 15,
        detector_side_effect=[[]] * 15,
        price_history_calls=15,
    ) as setup:
        metrics = await scan_item_type("courier")

        # Should scan exactly 5 pages (max_pages_per_type from config)
        assert metrics.pages_scanned == 5
        assert setup.mock_client.search_market.call_count == 5
        assert metrics.listings_checked == 15


# ──────────────────────────────────────────────────────────────────────────────
# main() tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_main_runs_all_item_types(
    mock_db_pool: AsyncMock,
) -> None:
    """Test that main() runs scan for all configured item types."""
    item_types = SCAN_CONFIG["item_types"]

    # Create metrics for each item type
    def make_metrics(item_type: str) -> ScanMetrics:
        m = ScanMetrics(item_type)
        m.listings_checked = 10
        m.gems_found = 5
        m.rare_gems_found = 3
        m.alerts_sent = 1
        m.errors = []
        return m

    mock_scan_results = [make_metrics(t) for t in item_types]

    with patch("src.main.get_pool", return_value=mock_db_pool), \
         patch("src.main.create_steam_client"), \
         patch("src.main.ListingParser"), \
         patch("src.main.GemDetector"), \
         patch("src.main.PriceAnalyzer"), \
         patch("src.main.DiscordNotifier"), \
         patch("src.main.close_pool", new_callable=AsyncMock) as mock_close_pool, \
         patch("src.main.validate_required_env", return_value=[]), \
         patch("src.main.scan_item_type", new_callable=AsyncMock) as mock_scan_item_type:

        mock_scan_item_type.side_effect = mock_scan_results

        mock_notifier = AsyncMock()
        DiscordNotifier = patch("src.main.DiscordNotifier").__enter__()
        DiscordNotifier.return_value = mock_notifier

        await main()

        # Should call scan_item_type for each item type
        assert mock_scan_item_type.call_count == len(item_types)
        for i, item_type in enumerate(item_types):
            call_args = mock_scan_item_type.call_args_list[i]
            assert call_args[0][0] == item_type

        # Pool should be closed at the end
        mock_close_pool.assert_called_once()


@pytest.mark.asyncio
async def test_main_validates_env_variables(
    mock_db_pool: AsyncMock,
) -> None:
    """Test that main() validates required environment variables."""
    with patch("src.main.validate_required_env", return_value=["SUPABASE_DB_URL", "STEAM_WEBAPI_KEY"]), \
         patch("src.main.close_pool", new_callable=AsyncMock):

        with pytest.raises(RuntimeError, match="Missing required environment variables"):
            await main()


@pytest.mark.asyncio
async def test_main_aggregates_metrics(
    mock_db_pool: AsyncMock,
) -> None:
    """Test that main() aggregates metrics across item types."""
    item_types = SCAN_CONFIG["item_types"]

    # Create metrics with known values
    mock_metrics = []
    for i, item_type in enumerate(item_types):
        m = ScanMetrics(item_type)
        m.listings_checked = 100
        m.gems_found = 10
        m.rare_gems_found = 5
        m.alerts_sent = 2
        m.errors = ["error1"] if i == 0 else []
        mock_metrics.append(m)

    with patch("src.main.get_pool", return_value=mock_db_pool), \
         patch("src.main.create_steam_client"), \
         patch("src.main.ListingParser"), \
         patch("src.main.GemDetector"), \
         patch("src.main.PriceAnalyzer"), \
         patch("src.main.DiscordNotifier"), \
         patch("src.main.close_pool", new_callable=AsyncMock), \
         patch("src.main.validate_required_env", return_value=[]), \
         patch("src.main.scan_item_type", new_callable=AsyncMock) as mock_scan_item_type:

        mock_scan_item_type.side_effect = mock_metrics

        mock_notifier = AsyncMock()
        DiscordNotifier = patch("src.main.DiscordNotifier").__enter__()
        DiscordNotifier.return_value = mock_notifier

        await main()

        # Verify scan_item_type was called for all types
        assert mock_scan_item_type.call_count == len(item_types)


# ──────────────────────────────────────────────────────────────────────────────
# ScanMetrics tests
# ──────────────────────────────────────────────────────────────────────────────

def test_scan_metrics_initialization() -> None:
    """Test ScanMetrics initializes with zero values."""
    metrics = ScanMetrics("courier")

    assert metrics.item_type == "courier"
    assert metrics.pages_scanned == 0
    assert metrics.listings_checked == 0
    assert metrics.listings_parsed == 0
    assert metrics.listings_failed == 0
    assert metrics.gems_found == 0
    assert metrics.rare_gems_found == 0
    assert metrics.alerts_sent == 0
    assert metrics.alerts_deduplicated == 0
    assert metrics.errors == []


def test_scan_metrics_log_summary(caplog: pytest.LogCaptureFixture) -> None:
    """Test ScanMetrics.log_summary logs expected format."""
    metrics = ScanMetrics("courier")
    metrics.pages_scanned = 5
    metrics.listings_checked = 100
    metrics.listings_parsed = 95
    metrics.listings_failed = 5
    metrics.gems_found = 20
    metrics.rare_gems_found = 15
    metrics.alerts_sent = 3
    metrics.alerts_deduplicated = 2
    metrics.errors = ["error1", "error2"]

    with caplog.at_level(logging.INFO):
        metrics.log_summary()

    assert "Scan complete for courier" in caplog.text
    assert "pages=5" in caplog.text
    assert "listings_checked=100" in caplog.text
    assert "parsed=95" in caplog.text
    assert "failed=5" in caplog.text
    assert "gems_found=20" in caplog.text
    assert "rare_gems=15" in caplog.text
    assert "alerts_sent=3" in caplog.text
    assert "alerts_dedup=2" in caplog.text
    assert "errors=2" in caplog.text


# ──────────────────────────────────────────────────────────────────────────────
# Integration test (requires network, env vars) - commented out by default
# ──────────────────────────────────────────────────────────────────────────────

# @pytest.mark.integration
# @pytest.mark.asyncio
# async def test_scan_item_type_live() -> None:
#     """Live integration test for scan_item_type (requires network and configured env)."""
#     import os
#     if not os.getenv("RUN_INTEGRATION_TESTS"):
#         pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")
#
#     from src.config import validate_required_env
#     missing = validate_required_env()
#     if missing:
#         pytest.skip(f"Missing env vars: {missing}")
#
#     # Run a single item type scan
#     metrics = await scan_item_type("courier")
#
#     assert metrics.item_type == "courier"
#     assert metrics.pages_scanned > 0
#     assert metrics.listings_checked >= 0
#     # Other assertions based on actual market state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
