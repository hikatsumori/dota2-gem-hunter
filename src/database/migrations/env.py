# Alembic environment configuration for Dota 2 Socket Scanner
# This file is used by Alembic to configure the migration environment

import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import urlparse

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

# Add project root to path so we can import src.config
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Import our settings to get the database URL
from src.config import get_db_url

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get database URL from our config module
raw_database_url = get_db_url()

# Debug: print raw URL (will show in GitHub Actions logs)
print(f"DEBUG: raw_database_url = {raw_database_url}")

# Parse the URL to extract components, avoiding psycopg2's URL parser issues
# with usernames containing dots (e.g., postgres.xxx@pooler.supabase.com)
def parse_database_url(url: str) -> dict:
    """Parse database URL into connection parameters."""
    parsed = urlparse(url)
    return {
        "drivername": "postgresql+psycopg2",
        "username": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
    }

db_params = parse_database_url(raw_database_url)

# If password is not in URL, get from PGPASSWORD env var (set in GitHub Actions)
import os
if db_params["password"] is None:
    db_params["password"] = os.environ.get("PGPASSWORD")

# Debug: print parsed components
print(f"DEBUG: db_params = {db_params}")

# Create SQLAlchemy URL object from parsed components (bypasses URL string parsing)
database_url = URL.create(**db_params)

# Debug: print final URL object
print(f"DEBUG: database_url = {database_url}")
print(f"DEBUG: database_url.username = {database_url.username}")
print(f"DEBUG: database_url.password = {'***' if database_url.password else 'None'}")

# target_metadata = None  # We'll use autogenerate
target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    Uses connect_args to bypass psycopg2's URL parser bug
    with usernames containing dots (postgres.xxx@pooler.supabase.com).
    """
    import psycopg2

    # Map db_params to psycopg2 connect arguments
    # psycopg2 uses: user, password, host, port, dbname
    connect_args = {
        "user": db_params["username"],
        "password": db_params["password"],
        "host": db_params["host"],
        "port": db_params["port"],
        "dbname": db_params["database"],
        # Force TCP connection (not unix socket)
        "connect_timeout": 10,
    }

    # Debug: print what we're passing to psycopg2 via connect_args
    print(f"DEBUG connect_args: user={connect_args['user']!r}, host={connect_args['host']!r}, port={connect_args['port']}, dbname={connect_args['dbname']!r}")

    connectable = create_engine(
        "postgresql+psycopg2://",
        connect_args=connect_args,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
