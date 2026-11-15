
"""Database connection pool for asyncpg."""

import os
from urllib.parse import unquote, urlparse

import asyncpg

from src.config import get_db_url


def parse_database_url(url: str) -> dict:
    """Parse database URL without changing the original host or port."""
    parsed = urlparse(url)

    return {
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/") or "postgres",
    }


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the async database connection pool."""
    global _pool

    if _pool is None:
        db_url = get_db_url()
        conn_params = parse_database_url(db_url)

        if not conn_params["password"]:
            conn_params["password"] = os.environ.get("PGPASSWORD")

        if not conn_params["password"]:
            raise RuntimeError(
                "Database password is missing. "
                "Check SUPABASE_DB_URL."
            )

        if not conn_params["host"] or not conn_params["user"]:
            raise RuntimeError(
                "Invalid database URL. Check SUPABASE_DB_URL."
            )

        _pool = await asyncpg.create_pool(
            **conn_params,
            ssl="require",
            min_size=1,
            max_size=2,
            command_timeout=30,
        )

    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None
