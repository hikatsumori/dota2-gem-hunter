"""
Discord notifier module for Dota 2 Steam Market Rare Socket Scanner.

Sends formatted alert messages to Discord via webhook with rate limiting,
retries, and proper error handling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

import httpx

from src.analyzer.price_analyzer import AlertMatch
from src.config import settings
from src.steam.models import Gem, GemType, Item, Listing

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token bucket rate limiter for Discord webhook messages.

    Limits to 30 messages per minute (1 message every 2 seconds).
    """

    def __init__(self, max_rate: int = 30, time_window: float = 60.0) -> None:
        """
        Initialize rate limiter.

        Args:
            max_rate: Maximum number of requests allowed in time_window
            time_window: Time window in seconds (default 60s for 30/min)
        """
        self.max_rate = max_rate
        self.time_window = time_window
        self._tokens: float = float(max_rate)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            now = time.monotonic()
            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            refill_amount = elapsed * (self.max_rate / self.time_window)
            self._tokens = min(self.max_rate, self._tokens + refill_amount)
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
                return

            # Need to wait for next token
            wait_time = (1 - self._tokens) * (self.time_window / self.max_rate)
            self._tokens = 0
            self._last_refill = now

        # Wait outside the lock
        await asyncio.sleep(wait_time)


class DiscordNotifier:
    """
    Sends alert notifications to Discord via webhook.

    Features:
    - Formatted embed messages with item details, gems, prices, and discounts
    - Rate limiting: max 30 messages/minute
    - Retries with exponential backoff on 429/5xx errors
    - Session reuse via httpx.AsyncClient
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        max_rate: int = 30,
        time_window: float = 60.0,
        max_retries: int = 3,
        base_backoff: float = 1.0,
        timeout: float = 10.0,
    ) -> None:
        """
        Initialize DiscordNotifier.

        Args:
            webhook_url: Discord webhook URL (defaults to config settings)
            max_rate: Max messages per time_window (default 30)
            time_window: Rate limit window in seconds (default 60)
            max_retries: Maximum retry attempts for failed requests
            base_backoff: Base backoff time in seconds for retries
            timeout: HTTP request timeout in seconds

        Raises:
            ValueError: If webhook_url is not provided and not in config
        """
        self._webhook_url = webhook_url or settings.discord_webhook_url
        if not self._webhook_url:
            raise ValueError(
                "Discord webhook URL not configured. "
                "Set DISCORD_WEBHOOK_URL environment variable or pass webhook_url parameter."
            )

        self._rate_limiter = RateLimiter(max_rate=max_rate, time_window=time_window)
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> DiscordNotifier:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _format_gems(self, gems: list[Gem]) -> str:
        """Format gems for Discord embed field, grouped by type."""
        if not gems:
            return "None"

        ethereal_gems = [g for g in gems if g.gem_type == GemType.ETHEREAL]
        prismatic_gems = [g for g in gems if g.gem_type == GemType.PRISMATIC]
        regular_gems = [g for g in gems if g.gem_type == GemType.REGULAR]

        parts: list[str] = []

        if ethereal_gems:
            eth_names = []
            for g in ethereal_gems:
                name = f"**{g.gem_name}**" if g.is_rare else g.gem_name
                if g.socket_index is not None:
                    name += f" (Socket {g.socket_index})"
                eth_names.append(name)
            parts.append(f"**Ethereal:** {', '.join(eth_names)}")

        if prismatic_gems:
            pri_names = []
            for g in prismatic_gems:
                name = f"**{g.gem_name}**" if g.is_rare else g.gem_name
                if g.socket_index is not None:
                    name += f" (Socket {g.socket_index})"
                pri_names.append(name)
            parts.append(f"**Prismatic:** {', '.join(pri_names)}")

        if regular_gems:
            reg_names = [g.gem_name for g in regular_gems]
            parts.append(f"Regular: {', '.join(reg_names)}")

        return "\n".join(parts)

    def _format_price(self, price: Decimal) -> str:
        """Format price as USD string."""
        return f"${price:.2f}"

    def _format_discount(self, discount: Decimal) -> str:
        """Format discount percentage."""
        return f"{discount:.2f}%"

    def _build_embed(
        self,
        alert_match: AlertMatch,
        listing: Listing,
        item: Item,
        gems: list[Gem],
    ) -> dict[str, Any]:
        """Build Discord embed payload."""
        # Determine color based on discount severity
        discount = float(alert_match.discount_pct)
        if discount >= 50:
            color = 0xFF0000  # Red for deep discounts
        elif discount >= 30:
            color = 0xFFA500  # Orange for significant discounts
        else:
            color = 0xFFFF00  # Yellow for moderate discounts

        # Build market link
        market_link = listing.listing_url or f"https://steamcommunity.com/market/listings/570/{item.market_hash_name}"

        embed = {
            "title": f"🎯 {item.name}",
            "color": color,
            "url": market_link,
            "fields": [
                {
                    "name": "Item",
                    "value": item.name,
                    "inline": True,
                },
                {
                    "name": "Gems",
                    "value": self._format_gems(gems),
                    "inline": False,
                },
                {
                    "name": "Price",
                    "value": self._format_price(alert_match.current_price),
                    "inline": True,
                },
                {
                    "name": "Avg Price (7d)",
                    "value": self._format_price(alert_match.average_price),
                    "inline": True,
                },
                {
                    "name": "Discount",
                    "value": self._format_discount(alert_match.discount_pct),
                    "inline": True,
                },
                {
                    "name": "Market Link",
                    "value": f"[View on Steam Market]({market_link})",
                    "inline": True,
                },
                {
                    "name": "Rule Matched",
                    "value": alert_match.rule_name,
                    "inline": True,
                },
            ],
            "footer": {
                "text": "Dota 2 Socket Scanner • Steam Market"
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        return embed

    async def _send_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        """Send webhook with retry logic for 429/5xx errors."""
        client = await self._get_client()

        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(self._webhook_url, json=payload)

                # Success
                if response.status_code < 400:
                    return response

                # Rate limited - respect Retry-After header
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        wait_time = float(retry_after)
                    else:
                        # Exponential backoff with jitter
                        wait_time = self._base_backoff * (2 ** attempt) + (attempt * 0.1)

                    logger.warning(
                        f"Discord rate limited (429), waiting {wait_time:.1f}s before retry "
                        f"(attempt {attempt + 1}/{self._max_retries + 1})"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                # Server errors - retry with backoff
                if 500 <= response.status_code < 600:
                    wait_time = self._base_backoff * (2 ** attempt) + (attempt * 0.1)
                    logger.warning(
                        f"Discord server error ({response.status_code}), "
                        f"waiting {wait_time:.1f}s before retry "
                        f"(attempt {attempt + 1}/{self._max_retries + 1})"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                # Client errors (4xx except 429) - don't retry, raise immediately
                logger.error(f"Discord webhook error {response.status_code}: {response.text}")
                response.raise_for_status()

            except httpx.HTTPStatusError:
                # This catches the raise_for_status() for 4xx errors (except 429 handled above)
                # Re-raise immediately without retry
                raise

            except httpx.TimeoutException:
                wait_time = self._base_backoff * (2 ** attempt) + (attempt * 0.1)
                logger.warning(
                    f"Discord webhook timeout, waiting {wait_time:.1f}s before retry "
                    f"(attempt {attempt + 1}/{self._max_retries + 1})"
                )
                await asyncio.sleep(wait_time)
                continue

            except httpx.RequestError as e:
                wait_time = self._base_backoff * (2 ** attempt) + (attempt * 0.1)
                logger.warning(
                    f"Discord webhook request error: {e}, "
                    f"waiting {wait_time:.1f}s before retry "
                    f"(attempt {attempt + 1}/{self._max_retries + 1})"
                )
                await asyncio.sleep(wait_time)
                continue

        # Exhausted retries
        raise RuntimeError(f"Failed to send Discord webhook after {self._max_retries + 1} attempts")

    async def send_alert(
        self,
        alert_match: AlertMatch,
        listing: Listing,
        item: Item,
        gems: list[Gem],
    ) -> None:
        """
        Send an alert notification to Discord.

        Args:
            alert_match: The matched alert rule with discount info
            listing: The Steam Market listing
            item: The item associated with the listing
            gems: All gems found in the listing (not just matched ones)

        Raises:
            RuntimeError: If webhook fails after all retries
            ValueError: If webhook URL is not configured
        """
        # Rate limiting
        await self._rate_limiter.acquire()

        # Build embed
        embed = self._build_embed(alert_match, listing, item, gems)

        # Discord webhook payload
        payload = {
            "embeds": [embed],
            "username": "Dota 2 Socket Scanner",
            "avatar_url": "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/570/69f3a1e3e8c6b5e8c8e8e8e8e8e8e8e8e8e8e8e8.png",
        }

        try:
            response = await self._send_with_retry(payload)
            logger.info(
                f"Discord alert sent successfully for {item.name} "
                f"(rule: {alert_match.rule_name}, discount: {alert_match.discount_pct}%)"
            )
        except Exception as e:
            logger.error(f"Failed to send Discord alert for {item.name}: {e}")
            raise


async def create_discord_notifier(
    webhook_url: str | None = None,
    max_rate: int = 30,
    time_window: float = 60.0,
    max_retries: int = 3,
    base_backoff: float = 1.0,
    timeout: float = 10.0,
) -> DiscordNotifier:
    """
    Convenience function to create a DiscordNotifier instance.

    Args:
        webhook_url: Discord webhook URL (defaults to config settings)
        max_rate: Max messages per time_window (default 30)
        time_window: Rate limit window in seconds (default 60)
        max_retries: Maximum retry attempts for failed requests
        base_backoff: Base backoff time in seconds for retries
        timeout: HTTP request timeout in seconds

    Returns:
        Configured DiscordNotifier instance
    """
    return DiscordNotifier(
        webhook_url=webhook_url,
        max_rate=max_rate,
        time_window=time_window,
        max_retries=max_retries,
        base_backoff=base_backoff,
        timeout=timeout,
    )