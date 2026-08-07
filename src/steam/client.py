"""
Steam WebAPI client for Dota 2 Market scanning.

Provides async HTTP client with rate limiting, rotating user agents,
exponential backoff, and session reuse for Steam Community Market
and Steam WebAPI endpoints.
"""

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from pydantic import ValidationError

from src.config import SCAN_CONFIG, settings
from src.steam.models import MarketSearchResponse

logger = logging.getLogger(__name__)

# Rotating user agents (modern browsers)
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

# Item type to search query mapping
ITEM_TYPE_QUERY_MAP: dict[str, str] = {
    "courier": "courier",
    "ward": "ward",
    "announcer": "announcer",
    "hud": "hud",
    "loading_screen": "loading screen",
    "emoticon": "emoticon",
    "chat_wheel": "chat wheel",
    "music_pack": "music pack",
}


class SteamWebClient:
    """
    Async HTTP client for Steam Community Market and WebAPI.

    Features:
    - Session reuse via httpx.AsyncClient
    - Rotating user agents per request
    - Rate limiting with configurable delay
    - Exponential backoff on 429/5xx errors (max retries)
    - Proper headers for Steam endpoints
    """

    def __init__(
        self,
        request_delay: float | None = None,
        max_retries: int | None = None,
        timeout_connect: float = 30.0,
        timeout_read: float = 60.0,
    ) -> None:
        """
        Initialize the Steam Web client.

        Args:
            request_delay: Seconds to wait between requests (default from SCAN_CONFIG)
            max_retries: Maximum retry attempts for failed requests (default from SCAN_CONFIG)
            timeout_connect: Connection timeout in seconds
            timeout_read: Read timeout in seconds
        """
        self._request_delay = request_delay or SCAN_CONFIG["request_delay_seconds"]
        self._max_retries = max_retries or SCAN_CONFIG["max_retries"]
        self._timeout = httpx.Timeout(
            connect=timeout_connect,
            read=timeout_read,
            write=30.0,
            pool=10.0,
        )
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "SteamWebClient":
        """Async context manager entry - creates HTTP client."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - closes HTTP client."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Create HTTP client if not exists."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                http2=True,
            )
            logger.debug("Created new httpx.AsyncClient")

    async def close(self) -> None:
        """Close the HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("Closed httpx.AsyncClient")

    def _get_headers(self, referer: str | None = None) -> dict[str, str]:
        """Generate request headers with rotating user agent."""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    async def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        async with self._lock:
            import time
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._request_delay:
                await asyncio.sleep(self._request_delay - elapsed)
            self._last_request_time = time.monotonic()

    async def _request_with_backoff(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make HTTP request with exponential backoff on 429/5xx.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional arguments passed to httpx request

        Returns:
            httpx.Response on success

        Raises:
            httpx.HTTPStatusError: On non-retryable errors or max retries exceeded
        """
        await self._ensure_client()
        await self._rate_limit()

        base_delay = self._request_delay
        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # Refresh headers for each attempt (rotate UA)
                headers = self._get_headers(kwargs.pop("referer", None))
                if "headers" in kwargs:
                    headers.update(kwargs["headers"])
                kwargs["headers"] = headers

                response = await self._client.request(method, url, **kwargs)  # type: ignore[union-attr]

                # Success (2xx)
                if response.is_success:
                    return response

                # Rate limited or server error - retry with backoff
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "Request failed (status=%d), attempt %d/%d, backing off %.1fs",
                        response.status_code,
                        attempt + 1,
                        self._max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Other HTTP errors - don't retry
                response.raise_for_status()

            except httpx.TimeoutException as e:
                last_exception = e
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Request timeout, attempt %d/%d, backing off %.1fs",
                    attempt + 1,
                    self._max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)

            except httpx.RequestError as e:
                last_exception = e
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Request error: %s, attempt %d/%d, backing off %.1fs",
                    e,
                    attempt + 1,
                    self._max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Max retries ({self._max_retries}) exceeded for {url}")

    async def search_market(self, item_type: str, page: int = 0) -> MarketSearchResponse:
        """
        Search Steam Community Market for Dota 2 items.

        Args:
            item_type: Type of item to search (courier, ward, announcer, hud, loading_screen)
            page: Page number (0-indexed), each page returns ~100 results

        Returns:
            MarketSearchResponse with search results

        Raises:
            ValueError: If item_type is not supported
            httpx.HTTPStatusError: On HTTP errors after retries
            ValidationError: If response doesn't match expected schema
        """
        query = ITEM_TYPE_QUERY_MAP.get(item_type.lower())
        if query is None:
            raise ValueError(
                f"Unsupported item_type: {item_type}. "
                f"Supported: {list(ITEM_TYPE_QUERY_MAP.keys())}"
            )

        params = {
            "query": query,
            "start": page * 100,
            "count": 100,
            "appid": SCAN_CONFIG["steam_app_id"],
            "norender": 1,  # Return JSON instead of HTML
        }

        url = SCAN_CONFIG["market_search_url"]
        referer = f"https://steamcommunity.com/market/search?appid={SCAN_CONFIG['steam_app_id']}"

        logger.info("Searching market: item_type=%s, page=%d, query=%s", item_type, page, query)

        response = await self._request_with_backoff(
            "GET", url, params=params, referer=referer
        )

        try:
            data = response.json()
            return MarketSearchResponse(**data)
        except ValidationError as e:
            logger.error("Failed to parse market search response: %s", e)
            raise
        except ValueError as e:
            logger.error("Invalid JSON in market search response: %s", e)
            raise

    async def fetch_listing_page(self, listing_url: str) -> str:
        """
        Fetch a Steam Market listing page HTML.

        Args:
            listing_url: Full URL to the listing page

        Returns:
            Raw HTML content of the listing page

        Raises:
            httpx.HTTPStatusError: On HTTP errors after retries
        """
        logger.info("Fetching listing page: %s", listing_url)

        response = await self._request_with_backoff(
            "GET", listing_url, referer="https://steamcommunity.com/market/"
        )

        return response.text

    async def get_item_schema(self) -> dict[str, Any]:
        """
        Fetch Dota 2 item schema from Steam WebAPI.

        Requires STEAM_WEBAPI_KEY environment variable.

        Returns:
            Parsed JSON response from GetSchema endpoint

        Raises:
            RuntimeError: If STEAM_WEBAPI_KEY is not configured
            httpx.HTTPStatusError: On HTTP errors after retries
        """
        api_key = settings.steam_webapi_key
        if not api_key:
            raise RuntimeError(
                "STEAM_WEBAPI_KEY not configured. "
                "Get a key from https://steamcommunity.com/dev/apikey"
            )

        url = "https://api.steampowered.com/IEconItems_570/GetSchema/v1/"
        params = {"key": api_key}

        logger.info("Fetching item schema from Steam WebAPI")

        response = await self._request_with_backoff("GET", url, params=params)

        try:
            return response.json()  # type: ignore[no-any-return]
        except ValueError as e:
            logger.error("Invalid JSON in item schema response: %s", e)
            raise


@asynccontextmanager
async def create_steam_client(
    request_delay: float | None = None,
    max_retries: int | None = None,
) -> AsyncIterator[SteamWebClient]:
    """
    Async context manager factory for SteamWebClient.

    Usage:
        async with create_steam_client() as client:
            results = await client.search_market("courier", 0)

    Args:
        request_delay: Seconds between requests (default from SCAN_CONFIG)
        max_retries: Max retry attempts (default from SCAN_CONFIG)

    Yields:
        Configured SteamWebClient instance
    """
    client = SteamWebClient(request_delay=request_delay, max_retries=max_retries)
    try:
        await client._ensure_client()
        yield client
    finally:
        await client.close()