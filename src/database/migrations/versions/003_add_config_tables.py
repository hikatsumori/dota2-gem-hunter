"""Add config tables: scan_config, populate alert_rules, config_audit

Revision ID: 003
Revises: 002
Create Date: 2026-08-10 00:00:00.000000

"""
import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


# Current hardcoded SCAN_CONFIG to seed the scan_config table
SCAN_CONFIG_DEFAULT = {
    "item_types": ["courier", "ward", "announcer", "hud", "loading_screen", "emoticon", "chat_wheel", "music_pack"],
    "max_pages_per_type": 5,
    "request_delay_seconds": 3,
    "max_retries": 3,
    "steam_app_id": 570,
    "market_search_url": "https://steamcommunity.com/market/search/render/",
    "listing_base_url": "https://steamcommunity.com/market/listings/570/",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# Current hardcoded ALERT_RULES to seed the alert_rules table
ALERT_RULES_DEFAULT = [
    {
        "name": "Ethereal Gem Under $100",
        "gem_type": "ethereal",
        "gem_names": [],
        "max_price_usd": 100.0,
        "min_discount_pct": 30.0,
        "is_active": True,
    },
    {
        "name": "Prismatic Gem Under $150",
        "gem_type": "prismatic",
        "gem_names": [],
        "max_price_usd": 150.0,
        "min_discount_pct": 25.0,
        "is_active": True,
    },
    {
        "name": "Any Rare Socket Deep Discount",
        "gem_type": "ethereal",
        "gem_names": [],
        "max_price_usd": 500.0,
        "min_discount_pct": 50.0,
        "is_active": True,
    },
]


def upgrade() -> None:
    # 1. Create scan_config table (single row with JSONB config)
    op.create_table(
        'scan_config',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, default=1),
        sa.Column('config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_by', sa.String(100), nullable=True),
    )
    # Enforce single row with unique constraint on constant value
    op.create_unique_constraint('scan_config_single_row', 'scan_config', ['id'])

    # Insert default scan_config
    op.execute(
        sa.text("""
            INSERT INTO scan_config (id, config, updated_by)
            VALUES (1, :config, 'system_migration')
        """).bindparams(config=json.dumps(SCAN_CONFIG_DEFAULT))
    )

    # 2. Create config_audit table
    op.create_table(
        'config_audit',
        sa.Column('id', sa.BigInteger(), nullable=False, primary_key=True),
        sa.Column('table_name', sa.String(50), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=True),
        sa.Column('old_data', sa.JSON(), nullable=True),
        sa.Column('new_data', sa.JSON(), nullable=True),
        sa.Column('changed_by', sa.String(100), nullable=True),
        sa.Column('changed_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('idx_config_audit_table_record', 'config_audit', ['table_name', 'record_id'])
    op.create_index('idx_config_audit_changed_at', 'config_audit', ['changed_at'])

    # 3. Add unique constraint on alert_rules.name (required for ON CONFLICT)
    op.create_unique_constraint('alert_rules_name_key', 'alert_rules', ['name'])

    # 4. Seed alert_rules table with default rules
    # Using INSERT with ON CONFLICT (name) DO NOTHING to be idempotent
    for rule in ALERT_RULES_DEFAULT:
        op.execute(
            sa.text("""
                INSERT INTO alert_rules (name, gem_type, gem_names, max_price_usd, min_discount_pct, is_active)
                VALUES (:name, :gem_type, :gem_names, :max_price_usd, :min_discount_pct, :is_active)
                ON CONFLICT (name) DO NOTHING
            """).bindparams(
                name=rule['name'],
                gem_type=rule['gem_type'],
                gem_names=rule['gem_names'],
                max_price_usd=rule['max_price_usd'],
                min_discount_pct=rule['min_discount_pct'],
                is_active=rule['is_active']
            )
        )


def downgrade() -> None:
    # Remove seeded alert_rules (only the exact ones we inserted)
    for rule in ALERT_RULES_DEFAULT:
        op.execute(
            sa.text("""
                DELETE FROM alert_rules WHERE name = :name
            """).bindparams(name=rule['name'])
        )

    # Drop tables
    op.drop_table('config_audit')
    op.drop_table('scan_config')
