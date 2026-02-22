"""phase 5: member_availability, mito_quotes, mito_titles

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # common.member_availability
    # ---------------------------------------------------------------------------
    op.create_table(
        "member_availability",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "member_id",
            sa.Integer(),
            sa.ForeignKey("common.guild_members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_of_week", sa.String(10), nullable=False),
        sa.Column("available", sa.Boolean(), server_default="true"),
        sa.Column("notes", sa.Text()),
        sa.Column("auto_signup", sa.Boolean(), server_default="false"),
        sa.Column("wants_reminders", sa.Boolean(), server_default="false"),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("member_id", "day_of_week", name="uq_member_day"),
        schema="common",
    )
    op.create_index(
        "ix_member_availability_member_id",
        "member_availability",
        ["member_id"],
        schema="common",
    )

    # ---------------------------------------------------------------------------
    # patt.mito_quotes
    # ---------------------------------------------------------------------------
    op.create_table(
        "mito_quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )

    # ---------------------------------------------------------------------------
    # patt.mito_titles
    # ---------------------------------------------------------------------------
    op.create_table(
        "mito_titles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_table("mito_titles", schema="patt")
    op.drop_table("mito_quotes", schema="patt")
    op.drop_index(
        "ix_member_availability_member_id",
        table_name="member_availability",
        schema="common",
    )
    op.drop_table("member_availability", schema="common")
