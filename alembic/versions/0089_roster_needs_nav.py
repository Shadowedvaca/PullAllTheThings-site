"""feat: add roster_needs admin nav entry (Officer+).

Revision ID: 0089
Revises: 0088
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Roster Needs moved to public /roster page — no admin nav entry needed.
    pass


def downgrade() -> None:
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'roster_needs'"
    )
