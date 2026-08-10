"""Database connection pool for asyncpg."""

from urllib.parse import urlparse

import asyncpg

from src.config import get_db_url


def parse_database_url(url: str) -> dict:
    """Parse database URL into connection parameters for asyncpg."""
    parsed = urlparse(url)
    username = parsed.username
    password = parsed.password
    host = parsed.hostname
    port = parsed.port or 5432
    database = parsed.path.lstrip("/")

    # Detect Supabase pooler URL and convert to direct connection
    # Pooler: host=aws-*-*.pooler.supabase.com, user=postgres.<project-ref>
    # Direct: host=db.<project-ref>.supabase.co, user=postgres, port=5432
    if host and host.endswith(".pooler.supabase.com") and username and username.startswith("postgres."):
        project_ref = username.split(".", 1)[1]  # Get part after "postgres."
        host = f"db.{project_ref}.supabase.co"
        port = 5432
        username = "postgres"
        print(f"DEBUG: Converted pooler URL to direct connection: host={host}, user={username}, port={port}")

    return {
        "user": username,
        "password": password,
        "host": host,
        "port": port,
        "database": database,
    }


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        db_url = get_db_url()
        conn_params = parse_database_url(db_url)
        # Fallback to PGPASSWORD env var if not in URL (for pooler connections)
        if conn_params["password"] is None:
            import os
            conn_params["password"] = os.environ.get("PGPASSWORD")
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
