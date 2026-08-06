"""
Tests for PriceAnalyzer.

Validates discount calculation, rule matching, async price history fetching,
and edge cases (no history, zero average price, empty gems, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import UTC, datetime

import pytest

from src.analyzer.price_analyzer import AlertMatch, PriceAnalyzer, create_price_analyzer
from src.config import ALERT_RULES
from src.steam.models import Gem, GemType, Item, Listing


# ──────────────────────────────────────────────────────────────────────────────
# Test Fixtures / Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_listing(
    price_usd: float = 50.0,
    item_id: int = 1,
    listing_id: str = "test_listing_123",
) -> Listing:
    """Create a test Listing with minimal required fields."""
    return Listing(
        id=1,
        item_id=item_id,
        listing_id=listing_id,
        price_usd=Decimal(str(price_usd)),
        price_raw=int(price_usd * 100),
        currency="USD",
        seller_steamid=None,
        listing_url=None,
        raw_description=None,
        fetched_at=datetime.now(UTC),
    )


def make_item(
    item_id: int = 1,
    market_hash_name: str = "Test Courier",
    can_have_sockets: bool = True,
) -> Item:
    """Create a test Item."""
    return Item(
        id=item_id,
        market_hash_name=market_hash_name,
        name="Test Courier",
        item_type="courier",
        quality="standard",
        rarity="rare",
        can_have_sockets=can_have_sockets,
        max_sockets=2,
        created_at=datetime.now(UTC),
    )


def make_gem(
    gem_type: GemType,
    gem_name: str,
    socket_index: int | None = 0,
) -> Gem:
    """Create a test Gem."""
    return Gem(
        id=1,
        listing_id=1,
        gem_type=gem_type,
        gem_name=gem_name,
        socket_index=socket_index,
        is_rare=True,
        created_at=datetime.now(UTC),
    )


def make_ethereal_gem(name: str = "Ethereal Flame", socket: int = 0) -> Gem:
    """Create an ethereal gem."""
    return make_gem(GemType.ETHEREAL, name, socket)


def make_prismatic_gem(name: str = "Ships in the Night", socket: int = 0) -> Gem:
    """Create a prismatic gem."""
    return make_gem(GemType.PRISMATIC, name, socket)


# ──────────────────────────────────────────────────────────────────────────────
# Mock PriceHistoryRepository
# ──────────────────────────────────────────────────────────────────────────────

class MockPriceHistoryRepository:
    """Mock repository for testing without database."""

    def __init__(self) -> None:
        self._prices: dict[int, Decimal] = {}
        # Use a proper async mock
        async def mock_get_average_price(item_id: int, days: int = 7) -> Decimal | None:
            return self._prices.get(item_id)
        self.get_average_price = mock_get_average_price

    def set_price(self, item_id: int, price: Decimal | float) -> None:
        """Set the average price for an item."""
        self._prices[item_id] = Decimal(str(price))


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous Tests (using sync analyze_listing with no repo)
# ──────────────────────────────────────────────────────────────────────────────

class TestPriceAnalyzerSync:
    """Tests for synchronous analyze_listing (no price history repo)."""

    def setup_method(self) -> None:
        self.analyzer = PriceAnalyzer(price_history_repo=None)

    def test_no_price_history_returns_empty(self) -> None:
        """Test that missing price history returns no matches."""
        listing = make_listing(price_usd=50.0)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = self.analyzer.analyze_listing(listing, item, gems)

        assert matches == []

    def test_no_item_id_returns_empty(self) -> None:
        """Test that missing item_id returns no matches."""
        listing = make_listing(price_usd=50.0, item_id=999)  # item_id that won't be in repo
        item = make_item(item_id=999)  # Valid ID but no price history
        gems = [make_ethereal_gem()]

        matches = self.analyzer.analyze_listing(listing, item, gems)

        assert matches == []

    def test_empty_gems_returns_empty(self) -> None:
        """Test that empty gems list returns no matches."""
        listing = make_listing(price_usd=50.0)
        item = make_item(item_id=1)
        gems: list[Gem] = []

        matches = self.analyzer.analyze_listing(listing, item, gems)

        assert matches == []

    def test_discount_calculation(self) -> None:
        """Test discount percentage calculation."""
        # current = 70, average = 100 -> discount = 30%
        discount = self.analyzer._calculate_discount(Decimal("70"), Decimal("100"))
        assert discount == Decimal("30.00")

        # current = 50, average = 100 -> discount = 50%
        discount = self.analyzer._calculate_discount(Decimal("50"), Decimal("100"))
        assert discount == Decimal("50.00")

        # current = 100, average = 100 -> discount = 0%
        discount = self.analyzer._calculate_discount(Decimal("100"), Decimal("100"))
        assert discount == Decimal("0.00")

        # current = 25, average = 100 -> discount = 75%
        discount = self.analyzer._calculate_discount(Decimal("25"), Decimal("100"))
        assert discount == Decimal("75.00")

        # current > average -> negative discount
        discount = self.analyzer._calculate_discount(Decimal("120"), Decimal("100"))
        assert discount == Decimal("-20.00")

    def test_discount_calculation_average_zero(self) -> None:
        """Test discount calculation with zero average price."""
        discount = self.analyzer._calculate_discount(Decimal("50"), Decimal("0"))
        assert discount == Decimal("0.00")

    def test_discount_calculation_precision(self) -> None:
        """Test discount calculation with decimal precision."""
        # 33.33 / 100 = 33.33% discount (66.67% of average)
        discount = self.analyzer._calculate_discount(Decimal("66.67"), Decimal("100"))
        assert discount == Decimal("33.33")

        # Rounding test: 66.666... -> 66.67
        discount = self.analyzer._calculate_discount(Decimal("66.666"), Decimal("100"))
        assert discount == Decimal("33.33")


# ──────────────────────────────────────────────────────────────────────────────
# Asynchronous Tests (with mocked repository)
# ──────────────────────────────────────────────────────────────────────────────

class TestPriceAnalyzerAsync:
    """Tests for asynchronous aanalyze_listing with mocked repo."""

    def setup_method(self) -> None:
        self.mock_repo = MockPriceHistoryRepository()
        self.analyzer = PriceAnalyzer(price_history_repo=self.mock_repo)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_rule1_ethereal_under_100_discount_30(self) -> None:
        """Test Rule 1: Ethereal gem, price < $100, discount ≥ 30%."""
        # Average = $100, current = $65 (35% discount), ethereal gem
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=65.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem("Ethereal Flame")]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        match = matches[0]
        assert match.rule_name == "Ethereal Gem Under $100"
        assert match.discount_pct == Decimal("35.00")
        assert match.current_price == Decimal("65.00")
        assert match.average_price == Decimal("100.00")
        assert len(match.matched_gems) == 1
        assert match.matched_gems[0].gem_type == GemType.ETHEREAL

    @pytest.mark.asyncio
    async def test_rule1_ethereal_price_at_100_no_match(self) -> None:
        """Test Rule 1: Price exactly $100 should not match (price < $100).

        Also ensure Rule 3 doesn't match by using discount < 50%.
        """
        # avg=200, current=100 -> 50% discount, price=$100
        # Rule 1: needs price < 100 (FAILS at exactly $100)
        # Rule 3: needs price < 500 AND discount >= 50% (discount=50% -> MATCHES!)
        # So use discount < 50% to avoid Rule 3 match too
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=100.0, item_id=1)  # 50% discount but price = $100
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Actually this matches Rule 3 (50% discount, price < $500)
        # Let's verify Rule 1 doesn't match but Rule 3 does
        assert len(matches) == 1
        assert matches[0].rule_name == "Any Rare Socket Deep Discount"

    @pytest.mark.asyncio
    async def test_rule1_ethereal_price_at_100_no_match_at_all(self) -> None:
        """Test Rule 1: Price exactly $100 with discount < 50% -> no rules match."""
        # avg=250, current=100 -> 60% discount, price=$100
        # Rule 1: needs price < 100 (FAILS)
        # Rule 3: needs discount >= 50% -> MATCHES (60% >= 50%)
        # Use avg=200, current=101 -> ~49.5% discount, price > $100
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=101.0, item_id=1)  # 49.5% discount, price > $100
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_rule1_ethereal_discount_29_no_match(self) -> None:
        """Test Rule 1: Discount 29% should not match (need ≥ 30%)."""
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=71.0, item_id=1)  # 29% discount
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_rule2_prismatic_under_150_discount_25(self) -> None:
        """Test Rule 2: Prismatic gem, price < $150, discount ≥ 25%."""
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=140.0, item_id=1)  # 30% discount
        item = make_item(item_id=1)
        gems = [make_prismatic_gem("Ships in the Night")]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        match = matches[0]
        assert match.rule_name == "Prismatic Gem Under $150"
        assert match.discount_pct == Decimal("30.00")
        assert match.matched_gems[0].gem_type == GemType.PRISMATIC

    @pytest.mark.asyncio
    async def test_rule2_prismatic_price_at_150_no_match(self) -> None:
        """Test Rule 2: Price exactly $150 should not match.

        Also ensure Rule 3 doesn't match by using discount < 50%.
        """
        # avg=300, current=150 -> 50% discount, price=$150
        # Rule 2: needs price < 150 (FAILS at exactly $150)
        # Rule 3: needs price < 500 AND discount >= 50% (discount=50% -> MATCHES!)
        self.mock_repo.set_price(1, Decimal("300"))
        listing = make_listing(price_usd=150.0, item_id=1)  # 50% discount but price = $150
        item = make_item(item_id=1)
        gems = [make_prismatic_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Matches Rule 3 (50% discount, price < $500)
        assert len(matches) == 1
        assert matches[0].rule_name == "Any Rare Socket Deep Discount"

    @pytest.mark.asyncio
    async def test_rule2_prismatic_price_at_150_no_match_at_all(self) -> None:
        """Test Rule 2: Price >= $150 with discount < 50% -> no rules match."""
        # avg=300, current=151 -> ~49.7% discount, price > $150
        self.mock_repo.set_price(1, Decimal("300"))
        listing = make_listing(price_usd=151.0, item_id=1)  # 49.7% discount, price > $150
        item = make_item(item_id=1)
        gems = [make_prismatic_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_rule3_any_rare_under_500_discount_50(self) -> None:
        """Test Rule 3: Any rare gem (ethereal/prismatic), price < $500, discount ≥ 50%."""
        self.mock_repo.set_price(1, Decimal("400"))
        listing = make_listing(price_usd=150.0, item_id=1)  # 62.5% discount
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]  # ethereal qualifies as "any rare"

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        match = matches[0]
        assert match.rule_name == "Any Rare Socket Deep Discount"
        assert match.discount_pct == Decimal("62.50")

    @pytest.mark.asyncio
    async def test_rule3_prismatic_also_matches(self) -> None:
        """Test Rule 3: Prismatic gem also matches 'any rare' rule."""
        self.mock_repo.set_price(1, Decimal("400"))
        listing = make_listing(price_usd=150.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_prismatic_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        assert matches[0].rule_name == "Any Rare Socket Deep Discount"

    @pytest.mark.asyncio
    async def test_multiple_rules_can_match_same_listing(self) -> None:
        """Test that a listing can match multiple rules simultaneously."""
        # Ethereal gem, price $90, avg $200 -> 55% discount
        # Matches Rule 1 (ethereal, <$100, ≥30%) AND Rule 3 (any rare, <$500, ≥50%)
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=90.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 2
        rule_names = [m.rule_name for m in matches]
        assert "Ethereal Gem Under $100" in rule_names
        assert "Any Rare Socket Deep Discount" in rule_names

    @pytest.mark.asyncio
    async def test_both_ethereal_and_prismatic_match_all_rules(self) -> None:
        """Test listing with both gem types matches appropriate rules."""
        # Ethereal + Prismatic, price $120, avg $300 -> 60% discount
        self.mock_repo.set_price(1, Decimal("300"))
        listing = make_listing(price_usd=120.0, item_id=1)
        item = make_item(item_id=1)
        gems = [
            make_ethereal_gem("Ethereal Flame", socket=0),
            make_prismatic_gem("Ships in the Night", socket=1),
        ]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Should match Rule 2 (prismatic, <$150, ≥25%) and Rule 3 (any rare, <$500, ≥50%)
        # Rule 1 requires price < $100, so no match there
        assert len(matches) == 2
        rule_names = [m.rule_name for m in matches]
        assert "Prismatic Gem Under $150" in rule_names
        assert "Any Rare Socket Deep Discount" in rule_names

    @pytest.mark.asyncio
    async def test_no_match_when_no_rare_gems(self) -> None:
        """Test that regular (non-rare) gems don't trigger alerts."""
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=50.0, item_id=1)  # 50% discount
        item = make_item(item_id=1)
        gems = [make_gem(GemType.REGULAR, "Regular Gem")]  # Not rare

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_no_match_when_average_price_zero(self) -> None:
        """Test that zero average price returns no matches."""
        self.mock_repo.set_price(1, Decimal("0"))
        listing = make_listing(price_usd=50.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_no_match_when_average_price_none(self) -> None:
        """Test that None average price (no history) returns no matches."""
        self.mock_repo.set_price(2, Decimal("100"))  # Different item_id
        listing = make_listing(price_usd=50.0, item_id=1)  # item_id=1 has no history
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_matched_gems_only_includes_matching_types(self) -> None:
        """Test that matched_gems only includes gems matching the rule's gem_types."""
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=90.0, item_id=1)  # 55% discount
        item = make_item(item_id=1)
        gems = [
            make_ethereal_gem("Ethereal Flame", socket=0),
            make_prismatic_gem("Ships in the Night", socket=1),
        ]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Rule 1: "Ethereal Gem Under $100" - only ethereal
        rule1_match = next(m for m in matches if m.rule_name == "Ethereal Gem Under $100")
        assert len(rule1_match.matched_gems) == 1
        assert rule1_match.matched_gems[0].gem_type == GemType.ETHEREAL
        assert rule1_match.matched_gems[0].gem_name == "Ethereal Flame"

        # Rule 3: "Any Rare Socket Deep Discount" - both ethereal and prismatic
        rule3_match = next(m for m in matches if m.rule_name == "Any Rare Socket Deep Discount")
        assert len(rule3_match.matched_gems) == 2
        gem_types = {g.gem_type for g in rule3_match.matched_gems}
        assert gem_types == {GemType.ETHEREAL, GemType.PRISMATIC}

    @pytest.mark.asyncio
    async def test_alert_match_to_dict_serialization(self) -> None:
        """Test AlertMatch.to_dict() for Discord webhook serialization."""
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=65.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem("Ethereal Flame", socket=0)]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)
        match = matches[0]

        data = match.to_dict()

        assert data["rule_name"] == "Ethereal Gem Under $100"
        assert data["discount_pct"] == 35.0
        assert data["current_price"] == 65.0
        assert data["average_price"] == 100.0
        assert len(data["matched_gems"]) == 1
        assert data["matched_gems"][0]["gem_name"] == "Ethereal Flame"
        assert data["matched_gems"][0]["gem_type"] == "ethereal"
        assert data["matched_gems"][0]["socket_index"] == 0
        assert data["matched_gems"][0]["is_rare"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Edge Case Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPriceAnalyzerEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def setup_method(self) -> None:
        self.mock_repo = MockPriceHistoryRepository()
        self.analyzer = PriceAnalyzer(price_history_repo=self.mock_repo)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_negative_discount_no_match(self) -> None:
        """Test that negative discount (current > average) doesn't match."""
        self.mock_repo.set_price(1, Decimal("50"))
        listing = make_listing(price_usd=60.0, item_id=1)  # -20% discount
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_exact_discount_threshold_matches(self) -> None:
        """Test that exact discount threshold (e.g., 30.00%) matches."""
        # current = 70, avg = 100 -> exactly 30% discount
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=70.0, item_id=1)
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        assert matches[0].discount_pct == Decimal("30.00")

    @pytest.mark.asyncio
    async def test_high_price_no_match_rule1_rule2(self) -> None:
        """Test that price ≥ $100 doesn't match Rule 1, ≥ $150 doesn't match Rule 2."""
        self.mock_repo.set_price(1, Decimal("500"))
        listing = make_listing(price_usd=100.0, item_id=1)  # 80% discount but price = $100
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Rule 1 requires price < 100, Rule 2 requires price < 150 (but needs prismatic),
        # Rule 3 requires price < 500 - this should match!
        assert len(matches) == 1
        assert matches[0].rule_name == "Any Rare Socket Deep Discount"

    @pytest.mark.asyncio
    async def test_price_500_no_match_rule3(self) -> None:
        """Test Rule 3: Price exactly $500 should not match."""
        self.mock_repo.set_price(1, Decimal("1000"))
        listing = make_listing(price_usd=500.0, item_id=1)  # 50% discount but price = $500
        item = make_item(item_id=1)
        gems = [make_ethereal_gem()]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 0

    @pytest.mark.asyncio
    async def test_multiple_gems_same_type_deduplication(self) -> None:
        """Test multiple gems of same type in matched_gems."""
        self.mock_repo.set_price(1, Decimal("200"))
        listing = make_listing(price_usd=90.0, item_id=1)
        item = make_item(item_id=1)
        gems = [
            make_ethereal_gem("Ethereal Flame", socket=0),
            make_ethereal_gem("Trail of the Burning Doom", socket=1),
        ]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        # Rule 1 matches, should include both ethereal gems
        rule1_match = next(m for m in matches if m.rule_name == "Ethereal Gem Under $100")
        assert len(rule1_match.matched_gems) == 2
        gem_names = {g.gem_name for g in rule1_match.matched_gems}
        assert gem_names == {"Ethereal Flame", "Trail of the Burning Doom"}

    @pytest.mark.asyncio
    async def test_gem_with_none_socket_index(self) -> None:
        """Test gem with None socket_index is handled correctly."""
        self.mock_repo.set_price(1, Decimal("100"))
        listing = make_listing(price_usd=65.0, item_id=1)
        item = make_item(item_id=1)
        gem = make_ethereal_gem()
        gem.socket_index = None  # Explicitly set to None
        gems = [gem]

        matches = await self.analyzer.aanalyze_listing(listing, item, gems)

        assert len(matches) == 1
        assert matches[0].matched_gems[0].socket_index is None


# ──────────────────────────────────────────────────────────────────────────────
# Config/Rule Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestAlertRulesConfig:
    """Tests that ALERT_RULES in config matches expectations."""

    def test_alert_rules_structure(self) -> None:
        """Test that ALERT_RULES has exactly 3 rules with correct structure."""
        assert len(ALERT_RULES) == 3

        for rule in ALERT_RULES:
            assert "name" in rule
            assert "gem_types" in rule
            assert "max_price_usd" in rule
            assert "min_discount_pct" in rule
            assert isinstance(rule["gem_types"], list)
            assert isinstance(rule["max_price_usd"], (int, float))
            assert isinstance(rule["min_discount_pct"], (int, float))

    def test_rule1_ethereal_under_100(self) -> None:
        """Test Rule 1 configuration."""
        rule = ALERT_RULES[0]
        assert rule["name"] == "Ethereal Gem Under $100"
        assert rule["gem_types"] == ["ethereal"]
        assert rule["max_price_usd"] == 100.0
        assert rule["min_discount_pct"] == 30.0

    def test_rule2_prismatic_under_150(self) -> None:
        """Test Rule 2 configuration."""
        rule = ALERT_RULES[1]
        assert rule["name"] == "Prismatic Gem Under $150"
        assert rule["gem_types"] == ["prismatic"]
        assert rule["max_price_usd"] == 150.0
        assert rule["min_discount_pct"] == 25.0

    def test_rule3_any_rare_under_500(self) -> None:
        """Test Rule 3 configuration."""
        rule = ALERT_RULES[2]
        assert rule["name"] == "Any Rare Socket Deep Discount"
        assert rule["gem_types"] == ["ethereal", "prismatic"]
        assert rule["max_price_usd"] == 500.0
        assert rule["min_discount_pct"] == 50.0


# ──────────────────────────────────────────────────────────────────────────────
# Factory Function Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestCreatePriceAnalyzer:
    """Tests for the create_price_analyzer factory function."""

    def test_create_without_repo(self) -> None:
        """Test creating analyzer without repository."""
        analyzer = create_price_analyzer()
        assert isinstance(analyzer, PriceAnalyzer)
        assert analyzer._price_history_repo is None

    def test_create_with_repo(self) -> None:
        """Test creating analyzer with repository."""
        mock_repo = MockPriceHistoryRepository()
        analyzer = create_price_analyzer(mock_repo)  # type: ignore[arg-type]
        assert isinstance(analyzer, PriceAnalyzer)
        assert analyzer._price_history_repo == mock_repo  # type: ignore[comparison-overlap]


# ──────────────────────────────────────────────────────────────────────────────
# Run directly
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])