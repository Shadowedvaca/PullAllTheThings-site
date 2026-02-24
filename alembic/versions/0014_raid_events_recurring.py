"""add recurring_event_id, auto_booked, raid_helper_payload to patt.raid_events

Revision ID: 0014
Revises: 0013
Create Date: 2026-02-24

Phase 3.4 — Admin Raid Tools
- Adds 3 columns to patt.raid_events linking to recurring_events + tracking auto-booking
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raid_events",
        sa.Column(
            "recurring_event_id",
            sa.Integer(),
            sa.ForeignKey("patt.recurring_events.id"),
            nullable=True,
        ),
        schema="patt",
    )
    op.add_column(
        "raid_events",
        sa.Column("auto_booked", sa.Boolean(), nullable=False, server_default="false"),
        schema="patt",
    )
    op.add_column(
        "raid_events",
        sa.Column("raid_helper_payload", postgresql.JSONB(), nullable=True),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("raid_events", "raid_helper_payload", schema="patt")
    op.drop_column("raid_events", "auto_booked", schema="patt")
    op.drop_column("raid_events", "recurring_event_id", schema="patt")
