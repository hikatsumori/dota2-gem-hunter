"""
Tests for DiscordNotifier.

Validates Discord embed formatting, rate limiting, retry logic,
and error handling with mocked httpx.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.analyzer.price_analyzer import AlertMatch
from src.notifier.discord import DiscordNotifier, RateLimiter, create_discord_notifier
from src.steam.models import Gem, GemType, Item, Listing

# ──────────────────────────────────────────────────────────────────────────────
# Test Fixtures / Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_listing(
    price_usd: float = 50.0,
    item_id: int = 1,
    listing_id: str = "test_listing_123",
    listing_url: str | None = "https://steamcommunity.com/market/listings/570/Test%20Item",
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
        listing_url=listing_url,
        raw_description=None,
        fetched_at=datetime.now(UTC),
    )


def make_item(
    item_id: int = 1,
    market_hash_name: str = "Test%20Courier",
    name: str = "Test Courier",
) -> Item:
    """Create a test Item."""
    return Item(
        id=item_id,
        market_hash_name=market_hash_name,
        name=name,
        item_type="courier",
        quality="standard",
        rarity="rare",
        can_have_sockets=True,
        max_sockets=2,
        created_at=datetime.now(UTC),
    )


def make_gem(
    gem_type: GemType,
    gem_name: str,
    socket_index: int | None = 0,
    is_rare: bool = True,
) -> Gem:
    """Create a test Gem."""
    return Gem(
        id=1,
        listing_id=1,
        gem_type=gem_type,
        gem_name=gem_name,
        socket_index=socket_index,
        is_rare=is_rare,
        created_at=datetime.now(UTC),
    )


def make_ethereal_gem(name: str = "Ethereal Flame", socket: int = 0, is_rare: bool = True) -> Gem:
    """Create an ethereal gem."""
    return make_gem(GemType.ETHEREAL, name, socket, is_rare)


def make_prismatic_gem(name: str = "Ships in the Night", socket: int = 0, is_rare: bool = True) -> Gem:
    """Create a prismatic gem."""
    return make_gem(GemType.PRISMATIC, name, socket, is_rare)


def make_alert_match(
    rule_name: str = "Ethereal Gem Under $100",
    discount_pct: float = 35.0,
    current_price: float = 65.0,
    average_price: float = 100.0,
) -> AlertMatch:
    """Create a test AlertMatch."""
    # We need at least one matched gem
    matched_gems = [make_ethereal_gem("Ethereal Flame")]
    return AlertMatch(
        rule_name=rule_name,
        discount_pct=Decimal(str(discount_pct)),
        matched_gems=matched_gems,
        current_price=Decimal(str(current_price)),
        average_price=Decimal(str(average_price)),
    )


# ──────────────────────────────────────────────────────────────────────────────
# RateLimiter Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestRateLimiter:
    """Tests for RateLimiter token bucket implementation."""

    @pytest.mark.asyncio
    async def test_acquire_allows_burst_up_to_max(self) -> None:
        """Test that burst up to max_rate is allowed immediately."""
        limiter = RateLimiter(max_rate=5, time_window=60.0)

        # Should allow 5 immediate acquisitions
        for _ in range(5):
            await limiter.acquire()

        # 6th should wait
        start = datetime.now()
        await limiter.acquire()
        elapsed = (datetime.now() - start).total_seconds()
        assert elapsed >= 1.9  # ~2 seconds for 30/min = 1/2 sec per token

    @pytest.mark.asyncio
    async def test_acquire_respects_rate_over_time(self) -> None:
        """Test that rate is respected over time window."""
        limiter = RateLimiter(max_rate=10, time_window=1.0)  # 10 per second for fast test

        # Use all tokens
        for _ in range(10):
            await limiter.acquire()

        # Wait for refill
        import asyncio
        await asyncio.sleep(1.1)

        # Should allow 10 more immediately
        start = datetime.now()
        for _ in range(10):
            await limiter.acquire()
        elapsed = (datetime.now() - start).total_seconds()
        assert elapsed < 0.5  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self) -> None:
        """Test concurrent acquire calls are serialized properly."""
        limiter = RateLimiter(max_rate=5, time_window=60.0)

        # Fire 5 concurrent acquires
        import asyncio
        await asyncio.gather(*[limiter.acquire() for _ in range(5)])

        # All should complete without waiting (within burst)


# ──────────────────────────────────────────────────────────────────────────────
# DiscordNotifier Tests - Formatting
# ──────────────────────────────────────────────────────────────────────────────

class TestDiscordNotifierFormatting:
    """Tests for Discord embed formatting."""

    def setup_method(self) -> None:
        self.notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test")

    def test_format_gems_ethereal_only(self) -> None:
        """Test formatting with only ethereal gems."""
        gems = [
            make_ethereal_gem("Ethereal Flame", socket=0),
            make_ethereal_gem("Trail of the Burning Doom", socket=1),
        ]
        result = self.notifier._format_gems(gems)
        assert "**Ethereal:**" in result
        assert "**Ethereal Flame**" in result  # Bold for rare
        assert "**Trail of the Burning Doom**" in result
        assert "Socket 0" in result
        assert "Socket 1" in result

    def test_format_gems_prismatic_only(self) -> None:
        """Test formatting with only prismatic gems."""
        gems = [
            make_prismatic_gem("Ships in the Night", socket=0),
            make_prismatic_gem("Corrupted Realm", socket=2),
        ]
        result = self.notifier._format_gems(gems)
        assert "**Prismatic:**" in result
        assert "**Ships in the Night**" in result
        assert "**Corrupted Realm**" in result

    def test_format_gems_mixed(self) -> None:
        """Test formatting with both ethereal and prismatic gems."""
        gems = [
            make_ethereal_gem("Ethereal Flame", socket=0),
            make_prismatic_gem("Ships in the Night", socket=1),
        ]
        result = self.notifier._format_gems(gems)
        assert "**Ethereal:**" in result
        assert "**Prismatic:**" in result

    def test_format_gems_regular_not_bold(self) -> None:
        """Test that regular (non-rare) gems are not bolded."""
        gem = make_gem(GemType.REGULAR, "Regular Gem", is_rare=False)
        gems = [gem]
        result = self.notifier._format_gems(gems)
        assert "Regular Gem" in result
        assert "**Regular Gem**" not in result  # Not bolded
        assert "Regular:" in result

    def test_format_gems_empty(self) -> None:
        """Test formatting empty gem list."""
        result = self.notifier._format_gems([])
        assert result == "None"

    def test_format_gems_none_socket_index(self) -> None:
        """Test formatting gems with None socket index."""
        gem = make_ethereal_gem("Ethereal Flame", socket=None)
        result = self.notifier._format_gems([gem])
        assert "Ethereal Flame" in result
        assert "Socket" not in result  # No socket index shown

    def test_format_price(self) -> None:
        """Test price formatting."""
        assert self.notifier._format_price(Decimal("50")) == "$50.00"
        assert self.notifier._format_price(Decimal("50.5")) == "$50.50"
        assert self.notifier._format_price(Decimal("0")) == "$0.00"

    def test_format_discount(self) -> None:
        """Test discount formatting."""
        assert self.notifier._format_discount(Decimal("30")) == "30.00%"
        assert self.notifier._format_discount(Decimal("33.33")) == "33.33%"
        assert self.notifier._format_discount(Decimal("50.5")) == "50.50%"

    def test_build_embed_structure(self) -> None:
        """Test that embed has all required fields."""
        alert_match = make_alert_match()
        listing = make_listing()
        item = make_item()
        gems = [make_ethereal_gem("Ethereal Flame", socket=0)]

        embed = self.notifier._build_embed(alert_match, listing, item, gems)

        assert embed["title"] == "🎯 Test Courier"
        assert embed["url"] == listing.listing_url
        assert "color" in embed
        assert len(embed["fields"]) == 7

        field_names = {f["name"] for f in embed["fields"]}
        expected_fields = {"Item", "Gems", "Price", "Avg Price (7d)", "Discount", "Market Link", "Rule Matched"}
        assert field_names == expected_fields

        # Check field values
        fields_dict = {f["name"]: f["value"] for f in embed["fields"]}
        assert fields_dict["Item"] == "Test Courier"
        assert "$65.00" in fields_dict["Price"]
        assert "$100.00" in fields_dict["Avg Price (7d)"]
        assert "35.00%" in fields_dict["Discount"]
        assert "Ethereal Gem Under $100" in fields_dict["Rule Matched"]
        assert "View on Steam Market" in fields_dict["Market Link"]

        assert "footer" in embed
        assert "timestamp" in embed

    def test_build_embed_color_by_discount(self) -> None:
        """Test embed color varies by discount severity."""
        # Deep discount >= 50% -> Red
        alert_match = make_alert_match(discount_pct=55.0)
        embed = self.notifier._build_embed(alert_match, make_listing(), make_item(), [])
        assert embed["color"] == 0xFF0000

        # Significant discount >= 30% -> Orange
        alert_match = make_alert_match(discount_pct=35.0)
        embed = self.notifier._build_embed(alert_match, make_listing(), make_item(), [])
        assert embed["color"] == 0xFFA500

        # Moderate discount < 30% -> Yellow
        alert_match = make_alert_match(discount_pct=25.0)
        embed = self.notifier._build_embed(alert_match, make_listing(), make_item(), [])
        assert embed["color"] == 0xFFFF00

    def test_build_embed_fallback_market_link(self) -> None:
        """Test market link falls back to hash_name when listing_url is None."""
        listing = make_listing(listing_url=None)
        item = make_item(market_hash_name="Test%20Courier")
        embed = self.notifier._build_embed(make_alert_match(), listing, item, [])
        assert "Test%20Courier" in embed["url"]


# ──────────────────────────────────────────────────────────────────────────────
# DiscordNotifier Tests - Sending (with mocked httpx)
# ──────────────────────────────────────────────────────────────────────────────

class TestDiscordNotifierSending:
    """Tests for send_alert with mocked HTTP client."""

    def setup_method(self) -> None:
        self.webhook_url = "https://discord.com/api/webhooks/test"
        self.notifier = DiscordNotifier(webhook_url=self.webhook_url, max_retries=2, base_backoff=0.01)

    @pytest.mark.asyncio
    async def test_send_alert_success(self) -> None:
        """Test successful alert send."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.text = ""

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            await self.notifier.send_alert(
                alert_match=make_alert_match(),
                listing=make_listing(),
                item=make_item(),
                gems=[make_ethereal_gem()],
            )

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == self.webhook_url
            assert "embeds" in call_args[1]["json"]
            assert len(call_args[1]["json"]["embeds"]) == 1

    @pytest.mark.asyncio
    async def test_send_alert_rate_limited(self) -> None:
        """Test rate limiter is called before send."""
        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            # Mock rate limiter acquire
            with patch.object(self.notifier._rate_limiter, "acquire", new_callable=AsyncMock) as mock_acquire:
                await self.notifier.send_alert(
                    alert_match=make_alert_match(),
                    listing=make_listing(),
                    item=make_item(),
                    gems=[make_ethereal_gem()],
                )
                mock_acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_retry_on_429(self) -> None:
        """Test retry on 429 rate limit with Retry-After header."""
        # First response: 429 with Retry-After
        response_429 = MagicMock()
        response_429.status_code = 429
        response_429.headers = {"Retry-After": "0.01"}  # Short for test

        # Second response: success
        response_ok = MagicMock()
        response_ok.status_code = 204

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[response_429, response_ok])
            mock_get_client.return_value = mock_client

            await self.notifier.send_alert(
                alert_match=make_alert_match(),
                listing=make_listing(),
                item=make_item(),
                gems=[make_ethereal_gem()],
            )

            assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_alert_retry_on_5xx(self) -> None:
        """Test retry on 5xx server errors."""
        response_500 = MagicMock()
        response_500.status_code = 500
        response_500.text = "Internal Server Error"

        response_ok = MagicMock()
        response_ok.status_code = 204

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[response_500, response_ok])
            mock_get_client.return_value = mock_client

            await self.notifier.send_alert(
                alert_match=make_alert_match(),
                listing=make_listing(),
                item=make_item(),
                gems=[make_ethereal_gem()],
            )

            assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_alert_no_retry_on_4xx_except_429(self) -> None:
        """Test no retry on 4xx client errors (except 429)."""
        response_400 = MagicMock()
        response_400.status_code = 400
        response_400.text = "Bad Request"
        # Configure raise_for_status to raise HTTPStatusError
        response_400.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=response_400
        ))

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=response_400)
            mock_get_client.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await self.notifier.send_alert(
                    alert_match=make_alert_match(),
                    listing=make_listing(),
                    item=make_item(),
                    gems=[make_ethereal_gem()],
                )

            # Should only try once, no retries
            assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_send_alert_retry_on_timeout(self) -> None:
        """Test retry on timeout."""
        response_ok = MagicMock()
        response_ok.status_code = 204

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[httpx.TimeoutException("Timeout"), response_ok])
            mock_get_client.return_value = mock_client

            await self.notifier.send_alert(
                alert_match=make_alert_match(),
                listing=make_listing(),
                item=make_item(),
                gems=[make_ethereal_gem()],
            )

            assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_send_alert_max_retries_exhausted(self) -> None:
        """Test exception raised after max retries exhausted."""
        response_500 = MagicMock()
        response_500.status_code = 500
        response_500.text = "Internal Server Error"

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=response_500)
            mock_get_client.return_value = mock_client

            with pytest.raises(RuntimeError, match="Failed to send Discord webhook after 3 attempts"):
                await self.notifier.send_alert(
                    alert_match=make_alert_match(),
                    listing=make_listing(),
                    item=make_item(),
                    gems=[make_ethereal_gem()],
                )

            # Initial + 2 retries = 3 attempts
            assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_send_alert_request_error_retry(self) -> None:
        """Test retry on httpx.RequestError."""
        response_ok = MagicMock()
        response_ok.status_code = 204

        with patch.object(self.notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=[httpx.RequestError("Connection failed"), response_ok])
            mock_get_client.return_value = mock_client

            await self.notifier.send_alert(
                alert_match=make_alert_match(),
                listing=make_listing(),
                item=make_item(),
                gems=[make_ethereal_gem()],
            )

            assert mock_client.post.call_count == 2


# ──────────────────────────────────────────────────────────────────────────────
# DiscordNotifier Tests - Configuration & Lifecycle
# ──────────────────────────────────────────────────────────────────────────────

class TestDiscordNotifierConfig:
    """Tests for DiscordNotifier configuration and lifecycle."""

    def test_webhook_url_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test webhook URL loaded from config/settings."""
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/from_env")

        # Need to reload settings
        from src.config import Settings
        settings = Settings()
        assert settings.discord_webhook_url == "https://discord.com/api/webhooks/from_env"

    def test_webhook_url_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test ValueError raised when webhook URL not provided."""
        # Patch the settings in the notifier module where it's used
        monkeypatch.setattr("src.notifier.discord.settings.discord_webhook_url", "")
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

        with pytest.raises(ValueError, match="Discord webhook URL not configured"):
            DiscordNotifier(webhook_url=None)

    def test_webhook_url_from_parameter(self) -> None:
        """Test webhook URL can be passed as parameter."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/param")
        assert notifier._webhook_url == "https://discord.com/api/webhooks/param"

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager properly closes client."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test")

        async with notifier as n:
            assert n is notifier
            client = await n._get_client()
            assert not client.is_closed

        # After exit, client should be closed
        # Note: We can't easily test this without accessing private members,
        # but the close() method is called

    @pytest.mark.asyncio
    async def test_close_client(self) -> None:
        """Test explicit close()."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test")
        client = await notifier._get_client()
        await notifier.close()
        assert client.is_closed

    def test_custom_rate_limit(self) -> None:
        """Test custom rate limit parameters."""
        notifier = DiscordNotifier(
            webhook_url="https://discord.com/api/webhooks/test",
            max_rate=10,
            time_window=30.0,
        )
        assert notifier._rate_limiter.max_rate == 10
        assert notifier._rate_limiter.time_window == 30.0

    def test_custom_retries_and_backoff(self) -> None:
        """Test custom retry parameters."""
        notifier = DiscordNotifier(
            webhook_url="https://discord.com/api/webhooks/test",
            max_retries=5,
            base_backoff=2.0,
        )
        assert notifier._max_retries == 5
        assert notifier._base_backoff == 2.0


# ──────────────────────────────────────────────────────────────────────────────
# Factory Function Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateDiscordNotifier:
    """Tests for create_discord_notifier factory function."""

    @pytest.mark.asyncio
    async def test_create_with_defaults(self) -> None:
        """Test factory creates notifier with defaults when webhook_url provided."""
        notifier = await create_discord_notifier(webhook_url="https://discord.com/api/webhooks/factory")
        assert isinstance(notifier, DiscordNotifier)
        assert notifier._webhook_url == "https://discord.com/api/webhooks/factory"
        await notifier.close()

    @pytest.mark.asyncio
    async def test_create_with_custom_params(self) -> None:
        """Test factory with custom parameters."""
        notifier = await create_discord_notifier(
            webhook_url="https://discord.com/api/webhooks/custom",
            max_rate=5,
            time_window=10.0,
            max_retries=1,
            base_backoff=0.5,
            timeout=5.0,
        )
        assert isinstance(notifier, DiscordNotifier)
        assert notifier._rate_limiter.max_rate == 5
        assert notifier._rate_limiter.time_window == 10.0
        assert notifier._max_retries == 1
        assert notifier._base_backoff == 0.5
        assert notifier._timeout == 5.0
        await notifier.close()


# ──────────────────────────────────────────────────────────────────────────────
# Integration-style Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestDiscordNotifierIntegration:
    """Integration-style tests combining multiple components."""

    @pytest.mark.asyncio
    async def test_full_alert_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test full pipeline: AlertMatch -> embed -> send."""
        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch.object(DiscordNotifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            notifier = await create_discord_notifier(webhook_url="https://test.webhook")

            # Complex gem setup
            gems = [
                make_ethereal_gem("Ethereal Flame", socket=0),
                make_ethereal_gem("Foulfell Shard", socket=1),
                make_prismatic_gem("Ships in the Night", socket=2),
            ]

            alert_match = AlertMatch(
                rule_name="Any Rare Socket Deep Discount",
                discount_pct=Decimal("62.50"),
                matched_gems=[gems[0], gems[1], gems[2]],
                current_price=Decimal("150.00"),
                average_price=Decimal("400.00"),
            )

            listing = make_listing(price_usd=150.0, listing_url="https://steamcommunity.com/market/listings/570/Test")
            item = make_item()

            await notifier.send_alert(alert_match, listing, item, gems)

            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            embed = payload["embeds"][0]

            # Verify all fields present
            assert embed["title"] == "🎯 Test Courier"
            assert "Ethereal Flame" in embed["fields"][1]["value"]  # Gems field
            assert "Foulfell Shard" in embed["fields"][1]["value"]
            assert "Ships in the Night" in embed["fields"][1]["value"]
            assert embed["color"] == 0xFF0000  # Red for 62.5% discount

            await notifier.close()

    @pytest.mark.asyncio
    async def test_multiple_alerts_rate_limited(self) -> None:
        """Test multiple alerts are rate limited."""
        mock_response = MagicMock()
        mock_response.status_code = 204

        notifier = DiscordNotifier(
            webhook_url="https://test.webhook",
            max_rate=2,  # Very low for testing
            time_window=1.0,  # 1 second window
            max_retries=0,
        )

        with patch.object(notifier, "_get_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            # Send 3 alerts - first 2 immediate, 3rd should wait
            import asyncio
            start = time.monotonic()

            await asyncio.gather(
                notifier.send_alert(make_alert_match(), make_listing(), make_item(), [make_ethereal_gem()]),
                notifier.send_alert(make_alert_match(), make_listing(), make_item(), [make_ethereal_gem()]),
                notifier.send_alert(make_alert_match(), make_listing(), make_item(), [make_ethereal_gem()]),
            )

            elapsed = time.monotonic() - start
            # With 2 per second, 3rd should wait ~0.5-1 second
            assert elapsed >= 0.4

        await notifier.close()


# ──────────────────────────────────────────────────────────────────────────────
# Run directly
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
