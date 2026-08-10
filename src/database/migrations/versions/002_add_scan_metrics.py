"""Add scan_metrics table for health monitoring.

Revision ID: 002
Revises: 001
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Scan metrics table for health monitoring
    op.create_table(
        'scan_metrics',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('scan_type', sa.String(length=50), nullable=False),
        sa.Column('listings_scanned', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gems_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('alerts_sent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errors', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration_seconds', sa.Numeric(precision=10, scale=3), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    # Index for time-based queries
    op.create_index('idx_scan_metrics_completed_at', 'scan_metrics', ['completed_at'], postgresql_using='btree')
    # Index for scan_type filtering
    op.create_index('idx_scan_metrics_type_time', 'scan_metrics', ['scan_type', 'completed_at'], postgresql_using='btree')


def downgrade() -> None:
    op.drop_index('idx_scan_metrics_type_time', table_name='scan_metrics')
    op.drop_index('idx_scan_metrics_completed_at', table_name='scan_metrics')
    op.drop_table('scan_metrics')
