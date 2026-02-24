"""add created_at to audit_issues

Revision ID: 0011
Revises: 0010
Create Date: 2026-02-24

The audit_issues table was created without a created_at column but the
ORM model and several queries reference it. Add it with a default of NOW()
so existing rows get the migration timestamp (close enough for audit history).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_issues",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            server_default=sa.text("NOW()"),
        ),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("audit_issues", "created_at", schema="guild_identity")
