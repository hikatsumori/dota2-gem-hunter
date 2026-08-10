"""Cleanup old data from the database - standalone script for GitHub Actions."""

import asyncio
import os

import asyncpg


async def cleanup() -> None:
    """Delete old data from listings, price_history, and alerts_sent tables."""
    conn = await asyncpg.connect(os.environ["SUPABASE_DB_URL"])
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
