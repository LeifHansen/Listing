"""listings (user_id, updated_at, id) index

The shape of `db.list_listings`, which is the query behind nearly every
seller-facing screen: filter on user_id, order by (updated_at DESC, id DESC),
take a page. The table had a single-column index on user_id, which serves the
filter and nothing else -- so the sort that follows it ran over every listing
the seller owns, on every page load and every "next page", and cost the most
for exactly the sellers who have the most (LIST_CAP is 3,000).

`id` is the third column because the keyset cursor breaks ties on it:
timestamps collide readily, since an import writes a whole store in one pass.

Every other keyset-paginated table already had its composite --
ix_notifications_user_unread, ix_error_events_last_seen,
ix_admin_audit_created. Listings is the largest and hottest table in the app
and was the one without.

Revision ID: 7b41c0d9e2a8
Revises: 22e5017c418a
Create Date: 2026-09-12 16:10:00.000000
"""
from __future__ import annotations

from alembic import op


revision = '7b41c0d9e2a8'
down_revision = '22e5017c418a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_listings_user_updated', 'listings',
                    ['user_id', 'updated_at', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_listings_user_updated', table_name='listings')
