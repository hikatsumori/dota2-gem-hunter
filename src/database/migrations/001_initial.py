"""Initial migration - create all tables for Dota 2 Socket Scanner

Revision ID: 001
Revises:
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Items that can have sockets
    op.create_table(
        'items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('market_hash_name', sa.String(length=500), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('item_type', sa.String(length=50), nullable=True),
        sa.Column('quality', sa.String(length=50), nullable=True),
        sa.Column('rarity', sa.String(length=50), nullable=True),
        sa.Column('can_have_sockets', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('max_sockets', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('market_hash_name')
    )

    # Individual market listings
    op.create_table(
        'listings',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('listing_id', sa.String(length=100), nullable=True),
        sa.Column('price_usd', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('price_raw', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='USD'),
        sa.Column('seller_steamid', sa.String(length=50), nullable=True),
        sa.Column('listing_url', sa.Text(), nullable=True),
        sa.Column('raw_description', sa.Text(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='SET NULL'),
    )

    # Gems found in listings
    op.create_table(
        'gems',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('listing_id', sa.BigInteger(), nullable=False),
        sa.Column('gem_type', sa.String(length=20), nullable=False),
        sa.Column('gem_name', sa.String(length=200), nullable=False),
        sa.Column('socket_index', sa.Integer(), nullable=True),
        sa.Column('is_rare', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
    )

    # Price history for trend analysis
    op.create_table(
        'price_history',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('min_price_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('avg_price_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('max_price_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('listing_count', sa.Integer(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='SET NULL'),
    )

    # Alert configuration (hardcoded rules in code, but schema for future)
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('gem_type', sa.String(length=20), nullable=True),
        sa.Column('gem_names', sa.ARRAY(sa.Text()), nullable=True),
        sa.Column('max_price_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('min_discount_pct', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # Sent alerts (deduplication)
    op.create_table(
        'alerts_sent',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('listing_id', sa.BigInteger(), nullable=True),
        sa.Column('rule_id', sa.Integer(), nullable=True),
        sa.Column('message_hash', sa.String(length=64), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id'], ondelete='SET NULL'),
    )

    # Indexes
    op.create_index('idx_listings_item_fetched', 'listings', ['item_id', 'fetched_at'], postgresql_using='btree')
    op.create_index('idx_gems_listing', 'gems', ['listing_id'], postgresql_using='btree')
    op.create_index('idx_gems_rare', 'gems', ['is_rare', 'gem_type'], postgresql_using='btree', postgresql_where=sa.text('is_rare = TRUE'))
    op.create_index('idx_price_history_item_time', 'price_history', ['item_id', 'recorded_at'], postgresql_using='btree')
    op.create_index('idx_alerts_listing', 'alerts_sent', ['listing_id'], postgresql_using='btree')


def downgrade() -> None:
    op.drop_index('idx_alerts_listing', table_name='alerts_sent')
    op.drop_index('idx_price_history_item_time', table_name='price_history')
    op.drop_index('idx_gems_rare', table_name='gems')
    op.drop_index('idx_gems_listing', table_name='gems')
    op.drop_index('idx_listings_item_fetched', table_name='listings')
    op.drop_table('alerts_sent')
    op.drop_table('alert_rules')
    op.drop_table('price_history')
    op.drop_table('gems')
    op.drop_table('listings')
    op.drop_table('items')
