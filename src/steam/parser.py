"""
HTML parser for Steam Market listing pages.

Extracts item name, price, description HTML, and socketed gem information
from Dota 2 item listing pages on the Steam Community Market.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from src.steam.models import GemParseData, GemType, ListingPageData

logger = logging.getLogger(__name__)


class ListingParser:
    """
    Parses Steam Market listing page HTML to extract structured data.

    Handles variations in Steam's HTML structure with fallback selectors
    and logs parsing failures for debugging.
    """

    # Known socket indicator patterns in description
    SOCKET_PATTERNS = [
        r"Socket\s*\d*",
        r"Gem\s*Socket",
        r"Ethereal\s+Gem",
        r"Prismatic\s+Gem",
    ]

    def __init__(self) -> None:
        """Initialize the parser."""
        self._gem_db_cache: dict[str, tuple[str, GemType]] | None = None

    def _load_gem_database(self) -> dict[str, tuple[str, GemType]]:
        """Load canonical gem names with their original casing from the gem database."""
        if self._gem_db_cache is not None:
            return self._gem_db_cache

        import json
        from pathlib import Path

        gem_db_path = Path(__file__).parent.parent.parent / "data" / "gems.json"
        try:
            with open(gem_db_path) as f:
                data = json.load(f)
            gem_map: dict[str, tuple[str, GemType]] = {}
            for gem_type_str, gems in data.items():
                if gem_type_str == "ethereal":
                    gem_type = GemType.ETHEREAL
                elif gem_type_str == "prismatic":
                    gem_type = GemType.PRISMATIC
                else:
                    gem_type = GemType.REGULAR
                for gem in gems:
                    # Only use canonical name, not aliases - aliases can cause false partial matches
                    gem_map[gem["name"].lower()] = (gem["name"], gem_type)
            self._gem_db_cache = gem_map
            return gem_map
        except Exception as e:
            logger.warning("Failed to load gem database: %s", e)
            self._gem_db_cache = {}
            return {}

    def parse_listing(self, html: str, listing_url: str = "") -> ListingPageData:
        """
        Parse a Steam Market listing page HTML.

        Args:
            html: Raw HTML content of the listing page
            listing_url: Optional URL of the listing page

        Returns:
            ListingPageData with extracted information

        Raises:
            ValueError: If HTML is empty or parsing fails critically
        """
        if not html or not html.strip():
            raise ValueError("Empty HTML content provided")

        soup = BeautifulSoup(html, "html.parser")

        # Extract listing ID from URL if available
        listing_id = self._extract_listing_id(listing_url)

        # Extract item name with fallbacks
        item_name = self._extract_item_name(soup)

        # Extract price with fallbacks
        price_usd, currency = self._extract_price(soup)

        # Extract description HTML
        description_html = self._extract_description(soup)

        # Extract seller steamid if available
        seller_steamid = self._extract_seller_steamid(soup)

        # Parse gems from description
        gems = self._parse_gems(soup, description_html)

        return ListingPageData(
            item_name=item_name,
            price_usd=price_usd,
            currency=currency,
            description_html=description_html,
            gems=gems,
            listing_url=listing_url,
            listing_id=listing_id,
            seller_steamid=seller_steamid,
        )

    def _extract_listing_id(self, listing_url: str) -> str | None:
        """Extract listing ID from the listing URL."""
        if not listing_url:
            return None
        try:
            parsed = urlparse(listing_url)
            # Steam listing URLs typically have format:
            # https://steamcommunity.com/market/listings/570/Item%20Name?m=123456
            query_params = parse_qs(parsed.query)
            if "m" in query_params:
                return query_params["m"][0]
            # Or look for listingid in path
            path_parts = parsed.path.split("/")
            for part in path_parts:
                if part.isdigit() and len(part) > 10:
                    return part
        except Exception as e:
            logger.debug("Could not extract listing ID from URL: %s", e)
        return None

    def _extract_item_name(self, soup: BeautifulSoup) -> str:
        """
        Extract item name from the listing page.

        Tries multiple selectors in order of preference.
        """
        # Primary: large image alt text
        selectors = [
            "#market_listing_largeimage img[alt]",
            ".market_listing_largeimage img[alt]",
            "#market_listing_item_name",
            ".market_listing_item_name",
            ".market_listing_item_name_block",
            "h1.market_listing_item_name",
            "[class*='market_listing_item_name']",
        ]

        for selector in selectors:
            try:
                elements = soup.select(selector)
                for el in elements:
                    if el.name == "img":
                        alt_attr = el.get("alt")
                        if alt_attr and isinstance(alt_attr, str):
                            name = alt_attr.strip()
                            if name and "market_listing" not in name.lower():
                                return name
                    text = el.get_text(strip=True)
                    if text and len(text) > 2:
                        return text
            except Exception as e:
                logger.debug("Selector '%s' failed: %s", selector, e)

        # Fallback: try to find in title tag
        try:
            title = soup.find("title")
            if title:
                text = title.get_text(strip=True)
                # Steam title format: "Item Name - Steam Community Market"
                if " - Steam Community Market" in text:
                    return text.replace(" - Steam Community Market", "").strip()
        except Exception:
            pass

        logger.warning("Failed to extract item name from listing page")
        return "Unknown Item"

    def _extract_price(self, soup: BeautifulSoup) -> tuple[Decimal, str]:
        """
        Extract price from the listing page.

        Returns tuple of (price_usd as Decimal, currency code).
        """
        # Primary selectors for buy order / listing price
        price_selectors = [
            "#market_commodity_buyrequests .market_commodity_orders_header_promote",
            ".market_listing_price.market_listing_price_with_fee",
            ".market_listing_price",
            "#market_commodity_buyrequests span[class*='price']",
            "[id*='buyorder'] span[class*='price']",
            ".market_table_value .market_listing_price",
            "span.market_listing_price",
        ]

        for selector in price_selectors:
            try:
                elements = soup.select(selector)
                for el in elements:
                    text = el.get_text(strip=True)
                    price, curr = self._parse_price_text(text)
                    if price is not None:
                        return price, curr
            except Exception as e:
                logger.debug("Price selector '%s' failed: %s", selector, e)

        # Fallback: search for price patterns in page text
        try:
            page_text = soup.get_text()
            # Look for price patterns like $87.50, 87.50 USD, etc.
            price_matches = re.findall(
                r"[\$\£\€]\s*(\d+(?:[.,]\d{1,2})?)\s*(USD|EUR|GBP)?",
                page_text,
                re.IGNORECASE,
            )
            for match in price_matches:
                price_str = match[0].replace(",", ".")
                curr = match[1].upper() if match[1] else "USD"
                try:
                    return Decimal(price_str), curr
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Price regex fallback failed: %s", e)

        logger.warning("Failed to extract price from listing page")
        return Decimal("0.00"), "USD"

    def _parse_price_text(self, text: str) -> tuple[Decimal | None, str]:
        """Parse price from text like '$87.50' or '87.50 USD'."""
        if not text:
            return None, "USD"

        # Remove common currency symbols and whitespace
        cleaned = re.sub(r"[\$\£\€,\s]", "", text)
        # Extract numeric part
        match = re.search(r"(\d+(?:[.,]\d{1,2})?)", cleaned)
        if not match:
            return None, "USD"

        price_str = match.group(1).replace(",", ".")

        # Determine currency
        currency = "USD"
        if "€" in text or "EUR" in text.upper():
            currency = "EUR"
        elif "£" in text or "GBP" in text.upper():
            currency = "GBP"

        try:
            return Decimal(price_str), currency
        except Exception:
            return None, "USD"

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """
        Extract the full description HTML from the listing page.

        Returns the inner HTML of the description container.
        """
        desc_selectors = [
            "#market_listing_row_description",
            ".market_listing_row_description",
            "#description",
            ".description",
            ".market_listing_right_cell",
            "[class*='market_listing_description']",
            "div[class*='description']",
        ]

        for selector in desc_selectors:
            try:
                elements = soup.select(selector)
                for el in elements:
                    html = str(el)
                    if html and len(html) > 50:  # Reasonable description size
                        return html
            except Exception as e:
                logger.debug("Description selector '%s' failed: %s", selector, e)

        # Fallback: return body content if nothing else found
        try:
            body = soup.find("body")
            if body:
                return str(body)
        except Exception:
            pass

        logger.warning("Failed to extract description from listing page")
        return ""

    def _extract_seller_steamid(self, soup: BeautifulSoup) -> str | None:
        """Extract seller SteamID from the listing page if available."""
        # Seller info is often in a link to their profile
        try:
            seller_links = soup.select('a[href*="steamcommunity.com/profiles/"]')
            for link in seller_links:
                href_attr = link.get("href", "")
                href = href_attr if isinstance(href_attr, str) else ""
                match = re.search(r"steamcommunity\.com/profiles/(\d+)", href)
                if match:
                    return match.group(1)

            # Also check for vanity URLs
            vanity_links = soup.select('a[href*="steamcommunity.com/id/"]')
            for link in vanity_links:
                href_attr = link.get("href", "")
                href = href_attr if isinstance(href_attr, str) else ""
                match = re.search(r"steamcommunity\.com/id/([^/?#]+)", href)
                if match:
                    return f"vanity:{match.group(1)}"
        except Exception as e:
            logger.debug("Seller SteamID extraction failed: %s", e)

        return None

    def _parse_gems(self, soup: BeautifulSoup, description_html: str) -> list[GemParseData]:
        """
        Parse socketed gems from the description HTML.

        Extracts raw gem name strings from the description; actual matching
        against the gem database happens in GemDetector (Task 6).
        """
        gems: list[GemParseData] = []
        gem_db = self._load_gem_database()

        # Get canonical gem names sorted by length (longest first) to avoid partial matches
        canonical_names = sorted(gem_db.keys(), key=len, reverse=True)

        # Get item name to exclude from gem matching (avoid matching gems in item name)
        item_name = self._extract_item_name(soup).lower()

        # Strategy 1: Look for structured socket rows in description
        socket_selectors = [
            "div[class*='socket']",
            "tr[class*='socket']",
            "td[class*='socket']",
            "[class*='gem']",
            "[class*='Socket']",
            "[class*='Gem']",
        ]

        socket_elements: list[Any] = []
        for selector in socket_selectors:
            try:
                socket_elements.extend(soup.select(selector))
            except Exception as e:
                logger.debug("Socket selector '%s' failed: %s", selector, e)

        # Strategy 2: Parse from description text using canonical gem names
        desc_text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in desc_text.split("\n") if line.strip()]

        socket_index = 0
        in_socket_context = False  # Track if previous line was a socket label
        for _i, line in enumerate(lines):
            # Skip lines that are just the item name (to avoid matching gems in item name)
            if line.lower() == item_name:
                continue

            # Check if line mentions a socket
            is_socket_line = any(
                re.search(pattern, line, re.IGNORECASE) for pattern in self.SOCKET_PATTERNS
            )

            # If this line is a socket label (e.g., "Socket 0:"), set context and continue
            if is_socket_line and not any(name in line.lower() for name in canonical_names):
                in_socket_context = True
                # Try to extract socket index from the line
                socket_match = re.search(r"Socket\s*(\d+)", line, re.IGNORECASE)
                if socket_match:
                    socket_index = int(socket_match.group(1))
                continue

            # Check each canonical gem name against the line (longest first)
            matched_in_line = False
            for gem_name_lower in canonical_names:
                if gem_name_lower in line.lower():
                    canonical_name, gem_type = gem_db[gem_name_lower]
                    gems.append(
                        GemParseData(
                            gem_type=gem_type,
                            gem_name=canonical_name,
                            socket_index=socket_index if (is_socket_line or in_socket_context) else None,
                            is_rare=gem_type in (GemType.ETHEREAL, GemType.PRISMATIC),
                        )
                    )
                    matched_in_line = True
                    if is_socket_line or in_socket_context:
                        socket_index += 1
                    # Once we match a gem in this line, skip shorter names that might be substrings
                    break

            # Reset socket context if we didn't match a gem in this line and it wasn't a socket line
            if not matched_in_line and not is_socket_line:
                in_socket_context = False

        # Deduplicate gems: prefer entries with socket_index over those without
        # Group by gem name, keep the one with socket_index if available
        gem_by_name: dict[str, GemParseData] = {}
        for gem in gems:
            key = gem.gem_name.lower()
            if key not in gem_by_name:
                gem_by_name[key] = gem
            else:
                # Prefer the one with socket_index
                existing = gem_by_name[key]
                if gem.socket_index is not None and existing.socket_index is None:
                    gem_by_name[key] = gem

        unique_gems = list(gem_by_name.values())

        if not unique_gems:
            logger.debug("No gems found in listing description")

        return unique_gems


# Convenience function for direct usage
def parse_listing_page(html: str, listing_url: str = "") -> ListingPageData:
    """
    Parse a Steam Market listing page HTML.

    Args:
        html: Raw HTML content of the listing page
        listing_url: Optional URL of the listing page

    Returns:
        ListingPageData with extracted information
    """
    parser = ListingParser()
    return parser.parse_listing(html, listing_url)
