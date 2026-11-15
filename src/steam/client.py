
"""
Conservative async HTTP client for Steam Community Market.

Features:
- One HTTP request at a time across client instances
- Minimum 15-second interval between request starts
- No automatic retries
- Global cooldown after HTTP 403 and 429
- Stable User-Agent
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import ValidationError

from src.config import SCAN_CONFIG, settings
from src.steam.models import MarketSearchResponse

logger = logging.getLogger(__name__)

# Shared protection across all SteamWebClient instances.
_GLOBAL_REQUEST_LOCK = asyncio.Lock()
_LAST_REQUEST_TIME = 0.0
_BLOCKED_UNTIL = 0.0
_BLOCK_REASON = ""

# Cooldown defaults
HTTP_403_COOLDOWN = 3600
HTTP_429_COOLDOWN = 1800
MAX_RETRY_AFTER = 21600

ITEM_TYPE_QUERY_MAP: dict[str, str] = {
    "courier": "courier",
    "ward": "ward",
    "announcer": "announcer",
    "hud": "hud",
    "loading_screen": "loading screen",
}


class SteamWebClient:
    """Conservative HTTP client for Steam endpoints."""

    def __init__(
        self,
        request_delay: float | None = None,
        max_retries: int | None = None,
        timeout_connect: float = 15.0,
        timeout_read: float = 30.0,
    ) -> None:

        self._request_delay = (
            SCAN_CONFIG["request_delay_seconds"]
            if request_delay is None
            else request_delay
        )

        # Automatic retries are intentionally disabled.
        self._max_retries = 0

        self._timeout = httpx.Timeout(
            connect=timeout_connect,
            read=timeout_read,
            write=15.0,
            pool=15.0,
        )

        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SteamWebClient":
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        await self.close()

    async def _ensure_client(self) -> None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                http2=False,
                headers={
                    "User-Agent": SCAN_CONFIG["user_agent"],
                    "Accept": "text/html,application/xhtml+xml,application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

            logger.info("Steam HTTP client initialized")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.info("Steam HTTP client closed")

    def _get_headers(
        self,
        referer: str | None = None,
    ) -> dict[str, str]:

        headers = {}

        if referer:
            headers["Referer"] = referer

        return headers

    @staticmethod
    def _get_retry_after(response: httpx.Response) -> int:
        """Read Retry-After header, supporting seconds and HTTP dates."""

        value = response.headers.get("Retry-After")

        if not value:
            return HTTP_429_COOLDOWN

        try:
            seconds = int(value)
        except ValueError:
            try:
                retry_date = parsedate_to_datetime(value)

                if retry_date.tzinfo is None:
                    retry_date = retry_date.replace(tzinfo=UTC)

                seconds = int(
                    (retry_date - datetime.now(UTC)).total_seconds()
                )
            except (TypeError, ValueError, OverflowError):
                seconds = HTTP_429_COOLDOWN

        return max(
            HTTP_429_COOLDOWN,
            min(seconds, MAX_RETRY_AFTER),
        )

    async def _request_with_backoff(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:

        global _LAST_REQUEST_TIME
        global _BLOCKED_UNTIL
        global _BLOCK_REASON

        await self._ensure_client()

        referer = kwargs.pop("referer", None)

        request_headers = self._get_headers(referer)

        if "headers" in kwargs:
            request_headers.update(kwargs.pop("headers"))

        async with _GLOBAL_REQUEST_LOCK:

            now = time.monotonic()

            # Global cooldown after a Steam restriction.
            if now < _BLOCKED_UNTIL:
                remaining = int(_BLOCKED_UNTIL - now)

                raise RuntimeError(
                    f"Steam requests suspended: {_BLOCK_REASON}. "
                    f"Cooldown remaining: {remaining} seconds."
                )

            # Enforce minimum interval between request starts.
            wait_time = (
                self._request_delay
                - (now - _LAST_REQUEST_TIME)
            )

            if wait_time > 0:
                logger.info(
                    "Rate limit: waiting %.1f seconds",
                    wait_time,
                )
                await asyncio.sleep(wait_time)

            _LAST_REQUEST_TIME = time.monotonic()

            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=request_headers,
                    **kwargs,
                )

            except httpx.RequestError:
                logger.exception(
                    "Steam network request failed. No retry will be attempted."
                )
                raise

            status = response.status_code

            if status == 403:
                _BLOCKED_UNTIL = (
                    time.monotonic() + HTTP_403_COOLDOWN
                )
                _BLOCK_REASON = "HTTP 403 Forbidden"

                logger.error(
                    "Steam returned HTTP 403. "
                    "All further requests suspended for at least 60 minutes."
                )

                response.raise_for_status()

            if status == 429:
                cooldown = self._get_retry_after(response)

                _BLOCKED_UNTIL = time.monotonic() + cooldown
                _BLOCK_REASON = "HTTP 429 Too Many Requests"

                logger.error(
                    "Steam returned HTTP 429. "
                    "All further requests suspended for %d seconds.",
                    cooldown,
                )

                response.raise_for_status()

            if 500 <= status < 600:
                logger.warning(
                    "Steam server error: HTTP %d. No retry.",
                    status,
                )

            response.raise_for_status()

            return response

    async def search_market(
        self,
        item_type: str,
        page: int = 0,
    ) -> MarketSearchResponse:

        query = ITEM_TYPE_QUERY_MAP.get(item_type.lower())

        if query is None:
            raise ValueError(
                f"Unsupported item_type: {item_type}"
            )

        params = {
            "query": query,
            "start": page * 100,
            "count": SCAN_CONFIG.get("results_per_page", 10),
            "appid": SCAN_CONFIG["steam_app_id"],
            "norender": 1,
        }

        url = SCAN_CONFIG["market_search_url"]

        referer = (
            f"https://steamcommunity.com/market/search"
            f"?appid={SCAN_CONFIG['steam_app_id']}"
        )

        logger.info(
            "Searching Steam Market: type=%s, page=%d",
            item_type,
            page,
        )

        response = await self._request_with_backoff(
            "GET",
            url,
            params=params,
            referer=referer,
        )

        try:
            data = response.json()
            return MarketSearchResponse(**data)

        except ValidationError:
            logger.exception(
                "Invalid Steam Market response structure"
            )
            raise

        except ValueError:
            logger.exception(
                "Steam returned invalid JSON"
            )
            raise

    async def fetch_listing_page(
        self,
        listing_url: str,
    ) -> str:

        logger.info(
            "Fetching listing page: %s",
            listing_url,
        )

        response = await self._request_with_backoff(
            "GET",
            listing_url,
            referer="https://steamcommunity.com/market/",
        )

        return response.text

    async def get_item_schema(self) -> dict[str, Any]:

        api_key = settings.steam_webapi_key

        if not api_key:
            raise RuntimeError(
                "STEAM_WEBAPI_KEY is not configured"
            )

        url = (
            "https://api.steampowered.com/"
            "IEconItems_570/GetSchema/v1/"
        )

        response = await self._request_with_backoff(
            "GET",
            url,
            params={"key": api_key},
        )

        try:
            return response.json()

        except ValueError:
            logger.exception(
                "Invalid JSON returned by Steam WebAPI"
            )
            raise


@asynccontextmanager
async def create_steam_client(
    request_delay: float | None = None,
    max_retries: int | None = None,
) -> AsyncIterator[SteamWebClient]:

    client = SteamWebClient(
        request_delay=request_delay,
        max_retries=max_retries,
    )

    try:
        await client._ensure_client()
        yield client

    finally:
        await client.close()
