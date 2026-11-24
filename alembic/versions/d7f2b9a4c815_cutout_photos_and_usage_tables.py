"""cutout_photos and cutout_usage tables

Background removal moved onto the seller's own device: Apple Vision on
iPhone, ML Kit on Android, a model in the browser -- free, with Photoroom as
the cloud fallback. cutout_photos is the queue of listing photos waiting for
the device (and how each one went); cutout_usage counts every engine's
photos per month, app-wide, which is what holds Photoroom to
PHOTOROOM_MONTHLY_BUDGET_USD.

Revision ID: d7f2b9a4c815
Revises: c4e8a1d6b392
Create Date: 2026-11-24 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd7f2b9a4c815'
down_revision = 'c4e8a1d6b392'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('cutout_photos',
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=True),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=255), nullable=False),
    sa.Column('working_sha', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('session_id', 'name')
    )
    op.create_index('ix_cutout_photos_user_state', 'cutout_photos',
                    ['user_id', 'state'], unique=False)
    op.create_table('cutout_usage',
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('photos', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('period', 'provider', 'platform')
    )


def downgrade() -> None:
    op.drop_table('cutout_usage')
    op.drop_index('ix_cutout_photos_user_state', table_name='cutout_photos')
    op.drop_table('cutout_photos')
