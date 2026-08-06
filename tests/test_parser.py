"""
Tests for the ListingParser HTML parser.

Uses real listing HTML fixtures to validate parsing of item names, prices,
descriptions, and gem extraction from Steam Market listing pages.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.steam.models import GemType, ListingPageData
from src.steam.parser import ListingParser, parse_listing_page


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "listings"


def load_fixture(filename: str) -> str:
    """Load a test fixture HTML file."""
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


class TestListingParser:
    """Tests for ListingParser class."""

    def setup_method(self) -> None:
        """Create parser instance for each test."""
        self.parser = ListingParser()

    def test_parse_ethereal_flame_war_dog(self) -> None:
        """Test parsing Ethereal Flame War Dog listing with 2 ethereal gems."""
        html = load_fixture("ethereal_flame_war_dog.html")
        result = self.parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Ethereal%20Flame%20War%20Dog?m=123456")

        # Validate basic fields
        assert isinstance(result, ListingPageData)
        assert result.item_name == "Ethereal Flame War Dog"
        assert result.price_usd == Decimal("87.50")
        assert result.currency == "USD"
        assert "Ethereal Flame" in result.description_html
        assert "Trail of the Burning Doom" in result.description_html
        assert result.listing_url == "https://steamcommunity.com/market/listings/570/Ethereal%20Flame%20War%20Dog?m=123456"
        assert result.listing_id == "123456"
        assert result.seller_steamid == "76561198012345678"

        # Validate gems
        assert len(result.gems) == 2
        gem_names = [g.gem_name for g in result.gems]
        assert "Ethereal Flame" in gem_names
        assert "Trail of the Burning Doom" in gem_names

        # Validate gem types
        for gem in result.gems:
            assert gem.gem_type == GemType.ETHEREAL
            assert gem.is_rare is True

        # Validate socket indices (0 and 1)
        socket_indices = [g.socket_index for g in result.gems if g.socket_index is not None]
        assert 0 in socket_indices
        assert 1 in socket_indices

    def test_parse_prismatic_gems_divine_messenger(self) -> None:
        """Test parsing Prismatic Gems Divine Messenger with 3 prismatic gems."""
        html = load_fixture("prismatic_gems_divine_messenger.html")
        result = self.parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Prismatic%20Gems%20Divine%20Messenger?m=789012")

        assert result.item_name == "Prismatic Gems Divine Messenger"
        assert result.price_usd == Decimal("142.00")
        assert result.currency == "USD"
        assert result.listing_id == "789012"
        assert result.seller_steamid == "vanity:prismaticcollector"

        # Validate gems
        assert len(result.gems) == 3
        gem_names = [g.gem_name for g in result.gems]
        assert "Ships in the Night" in gem_names
        assert "Corrupted Realm" in gem_names
        assert "Emerald Nightmare" in gem_names

        # Validate gem types
        for gem in result.gems:
            assert gem.gem_type == GemType.PRISMATIC
            assert gem.is_rare is True

        # Validate socket indices
        socket_indices = [g.socket_index for g in result.gems if g.socket_index is not None]
        assert set(socket_indices) == {0, 1, 2}

    def test_parse_mixed_gems_legacy_ward(self) -> None:
        """Test parsing Mixed Gems Legacy Ward with 1 ethereal and 1 prismatic gem."""
        html = load_fixture("mixed_gems_legacy_ward.html")
        result = self.parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Mixed%20Gems%20Legacy%20Ward")

        assert result.item_name == "Mixed Gems Legacy Ward"
        assert result.price_usd == Decimal("56.25")
        assert result.currency == "USD"

        # Validate gems - one ethereal, one prismatic
        assert len(result.gems) == 2
        gem_types = {g.gem_name: g.gem_type for g in result.gems}
        assert gem_types["Luminous Gaze"] == GemType.ETHEREAL
        assert gem_types["Sunbaked Plateau"] == GemType.PRISMATIC

        assert all(g.is_rare for g in result.gems)

    def test_parse_plain_courier_no_sockets(self) -> None:
        """Test parsing Plain Courier with no sockets (edge case)."""
        html = load_fixture("plain_courier_no_sockets.html")
        result = self.parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Plain%20Courier%20No%20Sockets")

        assert result.item_name == "Plain Courier No Sockets"
        assert result.price_usd == Decimal("42.00")
        assert result.currency == "EUR"
        assert result.seller_steamid == "76561198055555555"

        # No gems should be found
        assert len(result.gems) == 0

    def test_parse_foulfell_shard_demon_edge(self) -> None:
        """Test parsing Foulfell Shard Demon Edge with alias matching."""
        html = load_fixture("foulfell_shard_demon_edge.html")
        result = self.parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Foulfell%20Shard%20Demon%20Edge?m=123456789012345678")

        assert result.item_name == "Foulfell Shard Demon Edge"
        assert result.price_usd == Decimal("199.99")
        assert result.currency == "USD"
        assert result.listing_id == "123456789012345678"

        # Validate gem - Foulfell Shard is an ethereal gem with aliases
        assert len(result.gems) == 1
        gem = result.gems[0]
        assert gem.gem_name == "Foulfell Shard"
        assert gem.gem_type == GemType.ETHEREAL
        assert gem.is_rare is True

    def test_parse_listing_empty_html_raises(self) -> None:
        """Test that empty HTML raises ValueError."""
        with pytest.raises(ValueError, match="Empty HTML content"):
            self.parser.parse_listing("")

    def test_parse_listing_whitespace_html_raises(self) -> None:
        """Test that whitespace-only HTML raises ValueError."""
        with pytest.raises(ValueError, match="Empty HTML content"):
            self.parser.parse_listing("   \n\t  ")

    def test_price_parsing_various_formats(self) -> None:
        """Test price parsing handles various formats."""
        parser = ListingParser()

        # USD formats
        assert parser._parse_price_text("$87.50") == (Decimal("87.50"), "USD")
        assert parser._parse_price_text("87.50 USD") == (Decimal("87.50"), "USD")
        assert parser._parse_price_text("$1,234.56") == (Decimal("1234.56"), "USD")

        # EUR formats
        assert parser._parse_price_text("€42.00") == (Decimal("42.00"), "EUR")
        assert parser._parse_price_text("42.00 EUR") == (Decimal("42.00"), "EUR")

        # GBP formats
        assert parser._parse_price_text("£100.00") == (Decimal("100.00"), "GBP")

        # Invalid
        assert parser._parse_price_text("") == (None, "USD")
        assert parser._parse_price_text("N/A") == (None, "USD")

    def test_convenience_function(self) -> None:
        """Test the parse_listing_page convenience function."""
        html = load_fixture("ethereal_flame_war_dog.html")
        result = parse_listing_page(html, "https://example.com")

        assert isinstance(result, ListingPageData)
        assert result.item_name == "Ethereal Flame War Dog"
        assert len(result.gems) == 2

    def test_listing_id_extraction_from_url(self) -> None:
        """Test listing ID extraction from various URL formats."""
        parser = ListingParser()

        # Standard format with ?m= param
        html = "<html><body><span class='market_listing_item_name'>Test</span><span class='market_listing_price'>$10.00</span></body></html>"
        result = parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Item?m=123456")
        assert result.listing_id == "123456"

        # URL without m param
        result = parser.parse_listing(html, "https://steamcommunity.com/market/listings/570/Item")
        assert result.listing_id is None

        # Empty URL
        result = parser.parse_listing(html, "")
        assert result.listing_id is None

    def test_seller_steamid_extraction(self) -> None:
        """Test seller SteamID extraction from profile links."""
        parser = ListingParser()

        # Profile ID format
        html = """
        <html><body>
            <span class='market_listing_item_name'>Test</span>
            <span class='market_listing_price'>$10.00</span>
            <a href="https://steamcommunity.com/profiles/76561198012345678">Seller</a>
        </body></html>
        """
        result = parser.parse_listing(html, "")
        assert result.seller_steamid == "76561198012345678"

        # Vanity URL format
        html = """
        <html><body>
            <span class='market_listing_item_name'>Test</span>
            <span class='market_listing_price'>$10.00</span>
            <a href="https://steamcommunity.com/id/customname">Seller</a>
        </body></html>
        """
        result = parser.parse_listing(html, "")
        assert result.seller_steamid == "vanity:customname"

    def test_description_html_extraction(self) -> None:
        """Test description HTML is extracted properly."""
        html = load_fixture("ethereal_flame_war_dog.html")
        result = self.parser.parse_listing(html, "")

        # Description should contain socket info
        assert "Socket 0" in result.description_html
        assert "Ethereal Flame" in result.description_html
        assert len(result.description_html) > 100


class TestParserEdgeCases:
    """Test edge cases and error handling."""

    def setup_method(self) -> None:
        self.parser = ListingParser()

    def test_missing_item_name_fallback(self) -> None:
        """Test fallback when item name selectors don't match."""
        # Minimal HTML with only title tag
        html = "<html><head><title>Fallback Item - Steam Community Market</title></head><body><span class='market_listing_price'>$10.00</span></body></html>"
        result = self.parser.parse_listing(html, "")
        assert result.item_name == "Fallback Item"

    def test_missing_price_fallback(self) -> None:
        """Test fallback when price selectors don't match."""
        html = "<html><body><span class='market_listing_item_name'>Test Item</span></body></html>"
        result = self.parser.parse_listing(html, "")
        # Should default to 0.00
        assert result.price_usd == Decimal("0.00")

    def test_missing_description_fallback(self) -> None:
        """Test fallback when description selectors don't match."""
        html = "<html><body><span class='market_listing_item_name'>Test</span><span class='market_listing_price'>$10.00</span></body></html>"
        result = self.parser.parse_listing(html, "")
        # Should return body content or empty string
        assert isinstance(result.description_html, str)

    def test_duplicate_gem_deduplication(self) -> None:
        """Test that duplicate gems are deduplicated."""
        # HTML with duplicate gem mentions
        html = """
        <html><body>
            <span class='market_listing_item_name'>Test</span>
            <span class='market_listing_price'>$10.00</span>
            <div>Socket 0: Ethereal Flame</div>
            <div>Socket 0: Ethereal Flame</div>
            <div>Ethereal Flame</div>
        </body></html>
        """
        result = self.parser.parse_listing(html, "")
        # Should only have one Ethereal Flame gem
        ethereal_gems = [g for g in result.gems if g.gem_name == "Ethereal Flame"]
        assert len(ethereal_gems) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])