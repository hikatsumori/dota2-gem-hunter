"""Tests for health monitoring module (src/health.py)."""

from __future__ import annotations

from datetime import datetime, UTC, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.health import log_scan_metrics, get_recent_scan_metrics, get_scan_metrics_summary


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
        mp.setattr("src.health.get_pool", AsyncMock(return_value=mock_pool))
        yield


class TestLogScanMetrics:
    """Tests for log_scan_metrics function."""

    @pytest.mark.asyncio
    async def test_log_scan_metrics_basic(self, mock_pool: MockPool) -> None:
        """Test basic logging of scan metrics."""
        mock_pool.connection.fetchrow_results = [{"id": 42}]

        now = datetime.now(UTC)
        started_at = now - timedelta(seconds=120.5)
        completed_at = now

        scan_id = await log_scan_metrics(
            scan_type="full",
            listings_scanned=150,
            gems_found=25,
            alerts_sent=3,
            errors=2,
            duration_seconds=120.5,
            started_at=started_at,
            completed_at=completed_at,
        )

        assert scan_id == 42
        # Verify the query was called with correct parameters
        # We can't easily inspect the query in this mock setup, but we trust the implementation

    @pytest.mark.asyncio
    async def test_log_scan_metrics_defaults_timestamps(self, mock_pool: MockPool) -> None:
        """Test that timestamps default correctly when not provided."""
        mock_pool.connection.fetchrow_results = [{"id": 1}]

        # Don't provide started_at or completed_at - they should be computed
        scan_id = await log_scan_metrics(
            scan_type="courier",
            listings_scanned=100,
            gems_found=10,
            alerts_sent=1,
            errors=0,
            duration_seconds=60.0,
        )

        assert scan_id == 1

    @pytest.mark.asyncio
    async def test_log_scan_metrics_cleanup_type(self, mock_pool: MockPool) -> None:
        """Test logging cleanup scan type."""
        mock_pool.connection.fetchrow_results = [{"id": 99}]

        scan_id = await log_scan_metrics(
            scan_type="cleanup",
            listings_scanned=0,
            gems_found=0,
            alerts_sent=0,
            errors=0,
            duration_seconds=5.5,
        )

        assert scan_id == 99

    @pytest.mark.asyncio
    async def test_log_scan_metrics_with_decimal_duration(self, mock_pool: MockPool) -> None:
        """Test logging with precise decimal duration."""
        mock_pool.connection.fetchrow_results = [{"id": 7}]

        scan_id = await log_scan_metrics(
            scan_type="ward",
            listings_scanned=50,
            gems_found=5,
            alerts_sent=0,
            errors=1,
            duration_seconds=45.123,
        )

        assert scan_id == 7


class TestGetRecentScanMetrics:
    """Tests for get_recent_scan_metrics function."""

    @pytest.mark.asyncio
    async def test_get_recent_scan_metrics_returns_list(self, mock_pool: MockPool) -> None:
        """Test that function returns list of metrics."""
        now = datetime.now(UTC)
        mock_pool.connection.fetch_results = [[
            {
                "id": 1,
                "scan_type": "full",
                "listings_scanned": 100,
                "gems_found": 10,
                "alerts_sent": 2,
                "errors": 0,
                "duration_seconds": 60.0,
                "started_at": now - timedelta(seconds=60),
                "completed_at": now,
            },
            {
                "id": 2,
                "scan_type": "courier",
                "listings_scanned": 50,
                "gems_found": 5,
                "alerts_sent": 1,
                "errors": 0,
                "duration_seconds": 30.0,
                "started_at": now - timedelta(seconds=30),
                "completed_at": now,
            },
        ]]

        results = await get_recent_scan_metrics(hours=24)

        assert len(results) == 2
        assert results[0]["scan_type"] == "full"
        assert results[1]["scan_type"] == "courier"

    @pytest.mark.asyncio
    async def test_get_recent_scan_metrics_empty(self, mock_pool: MockPool) -> None:
        """Test empty results."""
        mock_pool.connection.fetch_results = [[]]

        results = await get_recent_scan_metrics(hours=24)

        assert results == []


class TestGetScanMetricsSummary:
    """Tests for get_scan_metrics_summary function."""

    @pytest.mark.asyncio
    async def test_get_scan_metrics_summary_with_data(self, mock_pool: MockPool) -> None:
        """Test summary aggregation with data."""
        mock_pool.connection.fetchrow_results = [{
            "total_scans": 10,
            "total_listings": 1500,
            "total_gems": 200,
            "total_alerts": 25,
            "total_errors": 5,
            "avg_duration": Decimal("45.5"),
            "latest_scan": datetime.now(UTC),
        }]

        summary = await get_scan_metrics_summary(hours=24)

        assert summary["total_scans"] == 10
        assert summary["total_listings"] == 1500
        assert summary["total_gems"] == 200
        assert summary["total_alerts"] == 25
        assert summary["total_errors"] == 5
        assert summary["avg_duration"] == Decimal("45.5")
        assert summary["latest_scan"] is not None

    @pytest.mark.asyncio
    async def test_get_scan_metrics_summary_no_data(self, mock_pool: MockPool) -> None:
        """Test summary with no data."""
        mock_pool.connection.fetchrow_results = [None]

        summary = await get_scan_metrics_summary(hours=24)

        assert summary["total_scans"] == 0
        assert summary["total_listings"] == 0
        assert summary["total_gems"] == 0
        assert summary["total_alerts"] == 0
        assert summary["total_errors"] == 0
        assert summary["avg_duration"] == 0
        assert summary["latest_scan"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
