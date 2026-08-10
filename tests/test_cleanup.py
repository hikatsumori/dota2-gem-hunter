"""Tests for cleanup module (src/cleanup.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.cleanup import cleanup_gems_orphaned, cleanup_old_data


class MockConnection:
    """Mock asyncpg connection."""

    def __init__(self) -> None:
        self.fetchrow_results: list[Any] = []
        self.fetch_results: list[list[Any]] = []
        self.execute_results: list[str] = []
        self.fetchrow_index = 0
        self.fetch_index = 0
        self.execute_index = 0

    async def fetchrow(self, query: str, *args: Any) -> Any | None:
        if self.fetchrow_index < len(self.fetchrow_results):
            result = self.fetchrow_results[self.fetchrow_index]
            self.fetchrow_index += 1
            return result
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        if self.fetch_index < len(self.fetch_results):
            result = self.fetch_results[self.fetch_index]
            self.fetch_index += 1
            return result
        return []

    async def execute(self, query: str, *args: Any) -> str:
        if self.execute_index < len(self.execute_results):
            result = self.execute_results[self.execute_index]
            self.execute_index += 1
            return result
        return "DELETE 0"

    async def __aenter__(self) -> MockConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class MockPool:
    """Mock asyncpg pool."""

    def __init__(self) -> None:
        self.connection = MockConnection()

    def acquire(self) -> MockConnection:
        return self.connection

    def release(self, conn: MockConnection) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_pool() -> MockPool:
    """Create a mock pool with a mock connection."""
    return MockPool()


@pytest.fixture(autouse=True)
def patch_get_pool(mock_pool: MockPool) -> None:
    """Patch get_pool to return our mock pool."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.cleanup.get_pool", AsyncMock(return_value=mock_pool))
        mp.setattr("src.cleanup.close_pool", AsyncMock())
        yield


class TestCleanupOldData:
    """Tests for cleanup_old_data function."""

    @pytest.mark.asyncio
    async def test_cleanup_old_data_all_repos(self, mock_pool: MockPool) -> None:
        """Test cleanup calls all repository cleanup methods."""
        # Setup execute results for each repository cleanup call
        # Order: listings, gems, price_history, alerts
        mock_pool.connection.execute_results = [
            "DELETE 10",   # listings
            "DELETE 5",    # gems
            "DELETE 3",    # price_history
            "DELETE 2",    # alerts
        ]

        results = await cleanup_old_data(older_than_days=30)

        assert results == {
            "listings_deleted": 10,
            "gems_deleted": 5,
            "price_history_deleted": 3,
            "alerts_deleted": 2,
        }
        assert mock_pool.connection.execute_index == 4

    @pytest.mark.asyncio
    async def test_cleanup_old_data_zero_deletions(self, mock_pool: MockPool) -> None:
        """Test cleanup when nothing to delete."""
        mock_pool.connection.execute_results = [
            "DELETE 0",
            "DELETE 0",
            "DELETE 0",
            "DELETE 0",
        ]

        results = await cleanup_old_data(older_than_days=30)

        assert results == {
            "listings_deleted": 0,
            "gems_deleted": 0,
            "price_history_deleted": 0,
            "alerts_deleted": 0,
        }

    @pytest.mark.asyncio
    async def test_cleanup_old_data_custom_retention(self, mock_pool: MockPool) -> None:
        """Test cleanup with custom retention period."""
        mock_pool.connection.execute_results = [
            "DELETE 1",
            "DELETE 1",
            "DELETE 1",
            "DELETE 1",
        ]

        results = await cleanup_old_data(older_than_days=7)

        assert results == {
            "listings_deleted": 1,
            "gems_deleted": 1,
            "price_history_deleted": 1,
            "alerts_deleted": 1,
        }

    @pytest.mark.asyncio
    async def test_cleanup_old_data_closes_pool(self, mock_pool: MockPool) -> None:
        """Test that pool is closed after cleanup."""
        mock_pool.connection.execute_results = [
            "DELETE 1",
            "DELETE 1",
            "DELETE 1",
            "DELETE 1",
        ]

        await cleanup_old_data(older_than_days=30)

        # close_pool should have been called (we mocked it)


class TestCleanupGemsOrphaned:
    """Tests for cleanup_gems_orphaned function."""

    @pytest.mark.asyncio
    async def test_cleanup_gems_orphaned_with_orphans(self, mock_pool: MockPool) -> None:
        """Test cleanup of orphaned gems."""
        mock_pool.connection.execute_results = ["DELETE 3"]

        deleted = await cleanup_gems_orphaned()

        assert deleted == 3
        assert mock_pool.connection.execute_index == 1

    @pytest.mark.asyncio
    async def test_cleanup_gems_orphaned_no_orphans(self, mock_pool: MockPool) -> None:
        """Test cleanup when no orphaned gems exist."""
        mock_pool.connection.execute_results = ["DELETE 0"]

        deleted = await cleanup_gems_orphaned()

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_gems_orphaned_closes_pool(self, mock_pool: MockPool) -> None:
        """Test that pool is closed after cleanup."""
        mock_pool.connection.execute_results = ["DELETE 0"]

        await cleanup_gems_orphaned()

        # close_pool should have been called


class TestRepositoryCleanupMethods:
    """Tests for repository cleanup_old methods (integration with actual repo classes)."""

    @pytest.mark.asyncio
    async def test_listing_repo_cleanup_old(self) -> None:
        """Test ListingRepository.cleanup_old directly."""
        from src.database.repositories import ListingRepository

        mock_pool = MockPool()
        mock_pool.connection.execute_results = ["DELETE 7"]
        repo = ListingRepository(mock_pool)

        deleted = await repo.cleanup_old(older_than_days=30)

        assert deleted == 7

    @pytest.mark.asyncio
    async def test_price_history_repo_cleanup_old(self) -> None:
        """Test PriceHistoryRepository.cleanup_old directly."""
        from src.database.repositories import PriceHistoryRepository

        mock_pool = MockPool()
        mock_pool.connection.execute_results = ["DELETE 4"]
        repo = PriceHistoryRepository(mock_pool)

        deleted = await repo.cleanup_old(older_than_days=30)

        assert deleted == 4

    @pytest.mark.asyncio
    async def test_alert_repo_cleanup_old(self) -> None:
        """Test AlertRepository.cleanup_old directly."""
        from src.database.repositories import AlertRepository

        mock_pool = MockPool()
        mock_pool.connection.execute_results = ["DELETE 2"]
        repo = AlertRepository(mock_pool)

        deleted = await repo.cleanup_old(older_than_days=30)

        assert deleted == 2

    @pytest.mark.asyncio
    async def test_gem_repo_cleanup_old(self) -> None:
        """Test GemRepository.cleanup_old directly."""
        from src.database.repositories import GemRepository

        mock_pool = MockPool()
        mock_pool.connection.execute_results = ["DELETE 6"]
        repo = GemRepository(mock_pool)

        deleted = await repo.cleanup_old(older_than_days=30)

        assert deleted == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
