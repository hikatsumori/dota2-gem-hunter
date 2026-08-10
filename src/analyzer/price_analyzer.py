"""
Price analyzer module for Dota 2 Steam Market Rare Socket Scanner.

Analyzes listings against alert rules to detect undervalued rare socketed items.
Computes discount percentages using 7-day price history and matches against
configured alert rules for ethereal/prismatic gems.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.config import ALERT_RULES
from src.database.repositories import PriceHistoryRepository
from src.steam.models import Gem, GemType, Item, Listing


class AlertMatch(BaseModel):
    """
    Represents a match between a listing and an alert rule.

    When a listing's price is significantly below its 7-day average
    and contains gems matching the rule's criteria, an AlertMatch is created.
    """

    model_config = ConfigDict(from_attributes=True)

    rule_name: str = Field(..., description="Name of the matched alert rule")
    discount_pct: Decimal = Field(
        ..., ge=0, max_digits=5, decimal_places=2, description="Discount percentage from average price"
    )
    matched_gems: list[Gem] = Field(
        ..., description="Gems from the listing that matched the rule's gem_types"
    )
    current_price: Decimal = Field(
        ..., ge=0, max_digits=10, decimal_places=2, description="Current listing price in USD"
    )
    average_price: Decimal = Field(
        ..., ge=0, max_digits=10, decimal_places=2, description="7-day weighted average price in USD"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization (e.g., Discord webhook)."""
        return {
            "rule_name": self.rule_name,
            "discount_pct": float(self.discount_pct),
            "matched_gems": [
                {
                    "gem_name": gem.gem_name,
                    "gem_type": gem.gem_type.value,
                    "socket_index": gem.socket_index,
                    "is_rare": gem.is_rare,
                }
                for gem in self.matched_gems
            ],
            "current_price": float(self.current_price),
            "average_price": float(self.average_price),
        }


class PriceAnalyzer:
    """
    Analyzes Steam Market listings for rare socketed items priced below market average.

    Uses PriceHistoryRepository to fetch 7-day weighted average prices,
    calculates discount percentages, and matches against ALERT_RULES from config.

    Rules (from config.py):
    1. Ethereal gems, price < $100, discount ≥ 30%
    2. Prismatic gems, price < $150, discount ≥ 25%
    3. Any rare gem (ethereal/prismatic), price < $500, discount ≥ 50%
    """

    def __init__(self, price_history_repo: PriceHistoryRepository | None = None) -> None:
        """
        Initialize the PriceAnalyzer.

        Args:
            price_history_repo: Repository for fetching price history.
                                If None, a mock/placeholder is used for testing.
        """
        self._price_history_repo = price_history_repo
        self._alert_rules = ALERT_RULES

    def analyze_listing(
        self, listing: Listing, item: Item, gems: list[Gem]
    ) -> list[AlertMatch]:
        """
        Analyze a listing against all alert rules.

        Computes discount from 7-day average price and checks each rule.
        A listing can match multiple rules (returns all matches).

        Args:
            listing: The Steam Market listing to analyze
            item: The item associated with the listing
            gems: List of gems detected in the listing

        Returns:
            List of AlertMatch objects (empty if no rules match)
        """
        matches: list[AlertMatch] = []

        # Need item_id to fetch price history
        if item.id is None:
            return matches

        # Need gems to match rules
        if not gems:
            return matches

        # Get 7-day average price from history
        average_price = self._get_average_price(item.id)
        if average_price is None or average_price <= 0:
            # No price history available - cannot calculate discount
            return matches

        current_price = listing.price_usd

        # Calculate discount percentage: (1 - current/average) * 100
        discount_pct = self._calculate_discount(current_price, average_price)

        # Check each alert rule
        for rule in self._alert_rules:
            rule_matches = self._check_rule(
                rule=rule,
                listing=listing,
                gems=gems,
                current_price=current_price,
                average_price=average_price,
                discount_pct=discount_pct,
            )
            matches.extend(rule_matches)

        return matches

    def _get_average_price(self, item_id: int) -> Decimal | None:
        """
        Fetch 7-day weighted average price for an item.

        Args:
            item_id: Database ID of the item

        Returns:
            Weighted average price as Decimal, or None if no history
        """
        if self._price_history_repo is None:
            return None

        # This is an async method, but we're in a sync context.
        # The actual scanner orchestration will handle async properly.
        # For now, return None to indicate no history available in sync context.
        # In production, this would be called from an async context.
        return None

    async def aget_average_price(self, item_id: int) -> Decimal | None:
        """
        Async version to fetch 7-day weighted average price.

        Args:
            item_id: Database ID of the item

        Returns:
            Weighted average price as Decimal, or None if no history
        """
        if self._price_history_repo is None:
            return None
        return await self._price_history_repo.get_average_price(item_id, days=7)

    async def aanalyze_listing(
        self, listing: Listing, item: Item, gems: list[Gem]
    ) -> list[AlertMatch]:
        """
        Async version of analyze_listing that fetches price history.

        Args:
            listing: The Steam Market listing to analyze
            item: The item associated with the listing
            gems: List of gems detected in the listing

        Returns:
            List of AlertMatch objects (empty if no rules match)
        """
        matches: list[AlertMatch] = []

        if item.id is None:
            return matches

        if not gems:
            return matches

        average_price = await self.aget_average_price(item.id)
        if average_price is None or average_price <= 0:
            return matches

        current_price = listing.price_usd
        discount_pct = self._calculate_discount(current_price, average_price)

        for rule in self._alert_rules:
            rule_matches = self._check_rule(
                rule=rule,
                listing=listing,
                gems=gems,
                current_price=current_price,
                average_price=average_price,
                discount_pct=discount_pct,
            )
            matches.extend(rule_matches)

        return matches

    def _calculate_discount(self, current_price: Decimal, average_price: Decimal) -> Decimal:
        """
        Calculate discount percentage from average price.

        Formula: (1 - current_price / average_price) * 100

        Args:
            current_price: Current listing price
            average_price: 7-day weighted average price

        Returns:
            Discount percentage as Decimal (e.g., 30.0 for 30%)
        """
        if average_price <= 0:
            return Decimal("0")

        discount = (Decimal("1") - current_price / average_price) * Decimal("100")
        # Round to 2 decimal places
        return discount.quantize(Decimal("0.01"))

    def _check_rule(
        self,
        rule: dict[str, Any],
        listing: Listing,
        gems: list[Gem],
        current_price: Decimal,
        average_price: Decimal,
        discount_pct: Decimal,
    ) -> list[AlertMatch]:
        """
        Check a single alert rule against the listing.

        Args:
            rule: Alert rule dictionary from config
            listing: The listing being analyzed
            gems: Gems in the listing
            current_price: Current listing price
            average_price: 7-day average price
            discount_pct: Calculated discount percentage

        Returns:
            List of AlertMatch (empty or single match)
        """
        rule_gem_types: list[str] = rule.get("gem_types", [])
        max_price_usd: float = rule.get("max_price_usd", 0)
        min_discount_pct: float = rule.get("min_discount_pct", 0)

        # Check price threshold
        if current_price >= Decimal(str(max_price_usd)):
            return []

        # Check discount threshold
        if discount_pct < Decimal(str(min_discount_pct)):
            return []

        # Check gem type match - ANY gem in listing must match rule's gem_types
        matched_gems = self._match_gems(gems, rule_gem_types)
        if not matched_gems:
            return []

        # All conditions met - create AlertMatch
        match = AlertMatch(
            rule_name=rule["name"],
            discount_pct=discount_pct,
            matched_gems=matched_gems,
            current_price=current_price,
            average_price=average_price,
        )

        return [match]

    def _match_gems(self, gems: list[Gem], rule_gem_types: list[str]) -> list[Gem]:
        """
        Find gems that match the rule's gem_types.

        Args:
            gems: List of gems in the listing
            rule_gem_types: List of gem type strings from rule (e.g., ["ethereal", "prismatic"])

        Returns:
            List of matching Gem objects
        """
        matched = []
        rule_types_lower = {gt.lower() for gt in rule_gem_types}

        for gem in gems:
            gem_type_str = gem.gem_type.value.lower() if isinstance(gem.gem_type, GemType) else str(gem.gem_type).lower()
            if gem_type_str in rule_types_lower:
                matched.append(gem)

        return matched


def create_price_analyzer(price_history_repo: PriceHistoryRepository | None = None) -> PriceAnalyzer:
    """
    Convenience function to create a PriceAnalyzer instance.

    Args:
        price_history_repo: Optional PriceHistoryRepository for fetching price history

    Returns:
        Configured PriceAnalyzer instance
    """
    return PriceAnalyzer(price_history_repo)
