"""phase 2.8: scheduling, availability, and attendance schema

Revision ID: 0008
Revises: 0007
Create Date: 2026-02-23

Changes:
1. Add scheduling_weight to common.guild_ranks (0=Initiate, 5=Officer/GL)
2. Add timezone and auto_invite_events to guild_identity.players
3. Create patt.player_availability (replaces common.member_availability)
4. Create patt.raid_seasons
5. Create patt.raid_events
6. Create patt.raid_attendance
7. Drop common.member_availability (stale data, all player_ids were NULL)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # Step 1: Add scheduling_weight to common.guild_ranks
    # -------------------------------------------------------------------------

    op.add_column(
        "guild_ranks",
        sa.Column(
            "scheduling_weight",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="common",
    )

    # Populate correct weights by level
    conn.execute(sa.text("UPDATE common.guild_ranks SET scheduling_weight = 0 WHERE level = 1"))
    conn.execute(sa.text("UPDATE common.guild_ranks SET scheduling_weight = 1 WHERE level = 2"))
    conn.execute(sa.text("UPDATE common.guild_ranks SET scheduling_weight = 3 WHERE level = 3"))
    conn.execute(sa.text("UPDATE common.guild_ranks SET scheduling_weight = 5 WHERE level = 4"))
    conn.execute(sa.text("UPDATE common.guild_ranks SET scheduling_weight = 5 WHERE level = 5"))

    # -------------------------------------------------------------------------
    # Step 2: Add timezone and auto_invite_events to guild_identity.players
    # -------------------------------------------------------------------------

    op.add_column(
        "players",
        sa.Column(
            "timezone",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'America/Chicago'"),
        ),
        schema="guild_identity",
    )
    op.add_column(
        "players",
        sa.Column(
            "auto_invite_events",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="guild_identity",
    )

    # -------------------------------------------------------------------------
    # Step 3: Create patt.player_availability
    # -------------------------------------------------------------------------

    op.create_table(
        "player_availability",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.players.id"),
            nullable=False,
        ),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("earliest_start", sa.Time(), nullable=False),
        sa.Column("available_hours", sa.Numeric(3, 1), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6",
            name="ck_player_availability_day_range",
        ),
        sa.CheckConstraint(
            "available_hours > 0 AND available_hours <= 16",
            name="ck_player_availability_hours",
        ),
        sa.UniqueConstraint(
            "player_id",
            "day_of_week",
            name="uq_player_availability_player_day",
        ),
        schema="patt",
    )

    # -------------------------------------------------------------------------
    # Step 4: Create patt.raid_seasons
    # -------------------------------------------------------------------------

    op.create_table(
        "raid_seasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="patt",
    )

    # -------------------------------------------------------------------------
    # Step 5: Create patt.raid_events
    # -------------------------------------------------------------------------

    op.create_table(
        "raid_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "season_id",
            sa.Integer(),
            sa.ForeignKey("patt.raid_seasons.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("start_time_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_time_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raid_helper_event_id", sa.String(30), nullable=True),
        sa.Column("discord_channel_id", sa.String(25), nullable=True),
        sa.Column("log_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by_player_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.players.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="patt",
    )

    # -------------------------------------------------------------------------
    # Step 6: Create patt.raid_attendance
    # -------------------------------------------------------------------------

    op.create_table(
        "raid_attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("patt.raid_events.id"),
            nullable=False,
        ),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.players.id"),
            nullable=False,
        ),
        sa.Column("signed_up", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("attended", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "character_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.wow_characters.id"),
            nullable=True,
        ),
        sa.Column(
            "noted_absence", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "event_id",
            "player_id",
            name="uq_attendance_event_player",
        ),
        schema="patt",
    )

    # -------------------------------------------------------------------------
    # Step 7: Drop common.member_availability
    # All rows had NULL player_ids (stale data from pre-2.7); unrecoverable.
    # -------------------------------------------------------------------------

    op.drop_table("member_availability", schema="common")


def downgrade() -> None:
    raise NotImplementedError(
        "Phase 2.8 migration is not safely reversible. "
        "Restore from a database backup taken before running this migration."
    )
