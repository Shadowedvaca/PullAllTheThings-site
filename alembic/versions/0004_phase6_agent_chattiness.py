"""phase 6: add agent_enabled and agent_chattiness to campaigns

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "agent_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema="patt",
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "agent_chattiness",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("campaigns", "agent_chattiness", schema="patt")
    op.drop_column("campaigns", "agent_enabled", schema="patt")
