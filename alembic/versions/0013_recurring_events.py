"""add recurring_events table and raid-helper columns to discord_config

Revision ID: 0013
Revises: 0012
Create Date: 2026-02-24

Phase 3.1 — Admin Availability Dashboard + Event Day System
- New table: patt.recurring_events (event-day config driving schedule, raid tools, auto-booking)
- Adds 6 Raid-Helper config columns to common.discord_config
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # patt.recurring_events
    # -----------------------------------------------------------------------
    op.create_table(
        "recurring_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False, server_default="raid"),
        sa.Column(
            "day_of_week",
            sa.Integer(),
            nullable=False,
            comment="0=Mon … 6=Sun (ISO weekday)",
        ),
        sa.Column("default_start_time", sa.Time(), nullable=False, server_default="21:00"),
        sa.Column(
            "default_duration_minutes", sa.Integer(), nullable=False, server_default="120"
        ),
        sa.Column("discord_channel_id", sa.String(25), nullable=True),
        sa.Column(
            "raid_helper_template_id",
            sa.String(50),
            nullable=True,
            server_default="wowretail2",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_on_public", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_recurring_events_day_range"),
        schema="patt",
    )

    # -----------------------------------------------------------------------
    # common.discord_config — Raid-Helper config columns
    # -----------------------------------------------------------------------
    op.add_column(
        "discord_config",
        sa.Column("raid_helper_api_key", sa.String(200), nullable=True),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column("raid_helper_server_id", sa.String(25), nullable=True),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column("raid_creator_discord_id", sa.String(25), nullable=True),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column("raid_channel_id", sa.String(25), nullable=True),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column("raid_voice_channel_id", sa.String(25), nullable=True),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "raid_default_template_id",
            sa.String(50),
            nullable=True,
            server_default="wowretail2",
        ),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("discord_config", "raid_default_template_id", schema="common")
    op.drop_column("discord_config", "raid_voice_channel_id", schema="common")
    op.drop_column("discord_config", "raid_channel_id", schema="common")
    op.drop_column("discord_config", "raid_creator_discord_id", schema="common")
    op.drop_column("discord_config", "raid_helper_server_id", schema="common")
    op.drop_column("discord_config", "raid_helper_api_key", schema="common")
    op.drop_table("recurring_events", schema="patt")
