"""notifications (user_id, created_at) index

The shape of `db.list_notifications`: filter on user_id, order by created_at
DESC, take 50. The table had `ix_notifications_user_id` (the filter alone) and
`ix_notifications_user_unread` on (user_id, read_at) -- neither of which has
created_at anywhere in it, so the sort that follows the filter ran over every
notification the seller had ever received.

That read is not occasional. The shell polls /api/notifications every 60
seconds from every screen, in every open tab, and nothing prunes the table:
one row is minted per sale, so the cost grows with how well the seller is
doing and never comes back down. The same omission the listings index existed
to fix (7b41c0d9e2a8), on the app's most frequent read rather than its
largest.

An ASC btree serves ORDER BY ... DESC by scanning backwards, so this is not
declared DESC -- SQLAlchemy's Index() emits the plain form and the planner
walks it either way.

Revision ID: b1c7e3f5a204
Revises: 0586887c43a2
Create Date: 2026-09-15 23:10:00.000000
"""
from __future__ import annotations

from alembic import op


revision = 'b1c7e3f5a204'
down_revision = '0586887c43a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_notifications_user_created', 'notifications',
                    ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notifications_user_created', table_name='notifications')
