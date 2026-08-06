"""
Tests for SteamWebClient.
"""

import pytest
from src.steam.client import (
    SteamWebClient,
    create_steam_client,
    ITEM_TYPE_QUERY_MAP,
    USER_AGENTS,
)


def test_item_type_query_map() -> None:
    """Test that all required item types have query mappings."""
    expected_types = ["courier", "ward", "announcer", "hud", "loading_screen"]
    for item_type in expected_types:
        assert item_type in ITEM_TYPE_QUERY_MAP
        assert isinstance(ITEM_TYPE_QUERY_MAP[item_type], str)
        assert len(ITEM_TYPE_QUERY_MAP[item_type]) > 0


def test_user_agents() -> None:
    """Test that user agents list is populated with valid strings."""
    assert len(USER_AGENTS) >= 3
    for ua in USER_AGENTS:
        assert isinstance(ua, str)
        assert len(ua) > 0
        assert "Mozilla" in ua


def test_steam_client_init_defaults() -> None:
    """Test SteamWebClient initialization with defaults from SCAN_CONFIG."""
    from src.config import SCAN_CONFIG

    client = SteamWebClient()
    assert client._request_delay == SCAN_CONFIG["request_delay_seconds"]
    assert client._max_retries == SCAN_CONFIG["max_retries"]
    assert client._client is None


def test_steam_client_init_custom() -> None:
    """Test SteamWebClient initialization with custom values."""
    client = SteamWebClient(request_delay=5.0, max_retries=2)
    assert client._request_delay == 5.0
    assert client._max_retries == 2


def test_get_headers() -> None:
    """Test header generation includes required fields."""
    client = SteamWebClient()
    headers = client._get_headers()

    assert "User-Agent" in headers
    assert headers["User-Agent"] in USER_AGENTS
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Referer" not in headers  # Not included when None

    headers_with_referer = client._get_headers(referer="https://example.com")
    assert headers_with_referer["Referer"] == "https://example.com"


@pytest.mark.asyncio
async def test_client_context_manager() -> None:
    """Test async context manager creates and closes client."""
    async with create_steam_client() as client:
        assert isinstance(client, SteamWebClient)
        assert client._client is not None
        assert not client._client.is_closed

    # After context exit, client should be closed
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_client_explicit_close() -> None:
    """Test explicit close method."""
    client = SteamWebClient()
    await client._ensure_client()
    assert client._client is not None
    assert not client._client.is_closed

    await client.close()
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_search_market_invalid_item_type() -> None:
    """Test search_market raises ValueError for invalid item type."""
    async with create_steam_client() as client:
        with pytest.raises(ValueError, match="Unsupported item_type"):
            await client.search_market("invalid_type", 0)


# Integration tests (require network) - commented out by default
# Uncomment and run with: pytest -m integration
#
# import os
#
# @pytest.mark.integration
# @pytest.mark.asyncio
# async def test_search_market_live() -> None:
#     """Test live market search for couriers (requires network)."""
#     if not os.getenv("RUN_INTEGRATION_TESTS"):
#         pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")
#
#     async with create_steam_client(request_delay=3.0) as client:
#         response = await client.search_market("courier", 0)
#         assert isinstance(response, MarketSearchResponse)
#         assert response.success is True
#         assert response.total_count >= 0
#         assert isinstance(response.results, list)
#
#
# @pytest.mark.integration
# @pytest.mark.asyncio
# async def test_fetch_listing_page_live() -> None:
#     """Test live listing page fetch (requires network)."""
#     if not os.getenv("RUN_INTEGRATION_TESTS"):
#         pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")
#
#     async with create_steam_client(request_delay=3.0) as client:
#         # First get a real listing URL from search
#         search_response = await client.search_market("courier", 0)
#         if not search_response.results:
#             pytest.skip("No results found for courier search")
#
#         listing = search_response.results[0]
#         listing_url = f"https://steamcommunity.com/market/listings/570/{listing.hash_name}"
#
#         html = await client.fetch_listing_page(listing_url)
#         assert isinstance(html, str)
#         assert len(html) > 0
#         assert "<html" in html.lower() or "<!doctype html" in html.lower()
#
#
# @pytest.mark.integration
# @pytest.mark.asyncio
# async def test_get_item_schema_live() -> None:
#     """Test live item schema fetch (requires network and STEAM_WEBAPI_KEY)."""
#     if not os.getenv("RUN_INTEGRATION_TESTS"):
#         pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")
#
#     from src.config import settings
#     if not settings.steam_webapi_key:
#         pytest.skip("STEAM_WEBAPI_KEY not configured")
#
#     async with create_steam_client(request_delay=3.0) as client:
#         schema = await client.get_item_schema()
#         assert isinstance(schema, dict)
#         assert "result" in schema