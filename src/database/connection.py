"""Database connection pool for asyncpg."""

from urllib.parse import urlparse

import asyncpg

from src.config import get_db_url


def parse_database_url(url: str) -> dict:
    """Parse database URL into connection parameters for asyncpg."""
    parsed = urlparse(url)
    return {
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
    }


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        db_url = get_db_url()
        conn_params = parse_database_url(db_url)
        _pool = await asyncpg.create_pool(
            **conn_params,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
