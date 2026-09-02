"""superadmin role, account disable, admin audit log

The operator console: `users.role` decides who may open it (written only by
scripts/grant_superadmin.py), `users.disabled_at` is account suspension, and
`admin_audit_log` is the append-only record every admin mutation writes
before it runs. `ix_listings_status` backs the console's whole-table
status group-by.

`server_default='user'` on role is for the ALTER itself — adding a NOT NULL
column to a table with rows needs a value for them. The model's own default
is Python-side, and the agree-test does not compare server defaults.

Revision ID: f3a9c1d24e07
Revises: adcd7b70f0b3
Create Date: 2026-08-31 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'f3a9c1d24e07'
down_revision = 'adcd7b70f0b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('role', sa.String(length=16),
                                     nullable=False, server_default='user'))
    op.add_column('users', sa.Column('disabled_at',
                                     sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_listings_status', 'listings', ['status'], unique=False)
    op.create_table('admin_audit_log',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('actor_id', sa.String(length=64), nullable=False),
    sa.Column('actor_email', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=48), nullable=False),
    sa.Column('target_type', sa.String(length=16), nullable=False),
    sa.Column('target_id', sa.String(length=64), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('data', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_audit_log_actor_id'), 'admin_audit_log',
                    ['actor_id'], unique=False)
    op.create_index(op.f('ix_admin_audit_log_action'), 'admin_audit_log',
                    ['action'], unique=False)
    op.create_index(op.f('ix_admin_audit_log_target_id'), 'admin_audit_log',
                    ['target_id'], unique=False)
    op.create_index('ix_admin_audit_created', 'admin_audit_log',
                    ['created_at', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_admin_audit_created', table_name='admin_audit_log')
    op.drop_index(op.f('ix_admin_audit_log_target_id'),
                  table_name='admin_audit_log')
    op.drop_index(op.f('ix_admin_audit_log_action'),
                  table_name='admin_audit_log')
    op.drop_index(op.f('ix_admin_audit_log_actor_id'),
                  table_name='admin_audit_log')
    op.drop_table('admin_audit_log')
    op.drop_index('ix_listings_status', table_name='listings')
    op.drop_column('users', 'disabled_at')
    op.drop_column('users', 'role')
