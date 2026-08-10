"""Cleanup old data from the database - standalone script for GitHub Actions."""

import asyncio
import os
from urllib.parse import urlparse

import asyncpg


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


async def cleanup() -> None:
    """Delete old data from listings, price_history, and alerts_sent tables."""
    # Parse URL to get host/port/database, but use PGPASSWORD for password
    conn_params = parse_database_url(os.environ["SUPABASE_DB_URL"])
    # Use PGPASSWORD env var (set in GitHub Actions) for password
    # This avoids URL parsing issues with special characters in password
    conn_params["password"] = os.environ.get("PGPASSWORD")
    if not conn_params["password"]:
        raise RuntimeError("PGPASSWORD environment variable not set")
    conn = await asyncpg.connect(**conn_params)
    try:
        await conn.execute(
            """
            DELETE FROM listings WHERE fetched_at < NOW() - INTERVAL '30 days'
            """
        )
        await conn.execute(
            """
            DELETE FROM price_history WHERE recorded_at < NOW() - INTERVAL '30 days'
            """
        )
        await conn.execute(
            """
            DELETE FROM alerts_sent WHERE sent_at < NOW() - INTERVAL '30 days'
            """
        )
    finally:
        await conn.close()


def main() -> None:
    """Entry point for running as a script."""
    asyncio.run(cleanup())
    print("Cleanup completed")


if __name__ == "__main__":
    main()
