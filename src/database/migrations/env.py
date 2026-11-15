
"""Alembic environment configuration."""

import os
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2
from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

from src.config import get_db_url

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
import sys
sys.path.insert(0, str(PROJECT_ROOT))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

raw_database_url = get_db_url()
parsed = urlparse(raw_database_url)

db_params = {
    "username": unquote(parsed.username) if parsed.username else None,
    "password": unquote(parsed.password) if parsed.password else None,
    "host": parsed.hostname,
    "port": parsed.port or 5432,
    "database": parsed.path.lstrip("/") or "postgres",
}

if not db_params["password"]:
    db_params["password"] = os.environ.get("PGPASSWORD")

if not db_params["password"]:
    raise RuntimeError(
        "Database password is missing. Check SUPABASE_DB_URL."
    )

if not db_params["host"] or not db_params["username"]:
    raise RuntimeError(
        "Invalid database URL. Check SUPABASE_DB_URL."
    )

database_url = URL.create(
    drivername="postgresql+psycopg2",
    username=db_params["username"],
    password=db_params["password"],
    host=db_params["host"],
    port=db_params["port"],
    database=db_params["database"],
)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""
    context.configure(
        url=database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a live database connection."""
    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 10},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
