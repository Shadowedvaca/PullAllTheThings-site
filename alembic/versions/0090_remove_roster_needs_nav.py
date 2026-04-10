"""fix: remove erroneous roster_needs admin nav entry.

Roster Needs was briefly added as an admin nav entry before being moved
to the public /roster page. This migration cleans it up on any environment
where migration 0089 ran before the no-op was applied.

Revision ID: 0090
Revises: 0089
"""

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'roster_needs'"
    )


def downgrade() -> None:
    pass
