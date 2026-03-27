"""Add signup snapshot columns for attendance tracking

Revision ID: 0063
Revises: 0062
Create Date: 2026-03-26

Adds:
- patt.raid_events.signup_snapshot_at: stamped when signup snapshot job completes
- patt.raid_attendance.was_available: whether player had availability set for that day
- patt.raid_attendance.raid_helper_status: player's Raid-Helper signup state
- common.discord_config.attendance_excuse_if_unavailable: auto-excuse if not available
- common.discord_config.attendance_excuse_if_discord_absent: auto-excuse if marked Absence in RH
"""
from alembic import op
import sqlalchemy as sa

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raid_events",
        sa.Column("signup_snapshot_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("was_available", sa.Boolean(), nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("raid_helper_status", sa.String(20), nullable=True),
        schema="patt",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_excuse_if_unavailable",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_excuse_if_discord_absent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("discord_config", "attendance_excuse_if_discord_absent", schema="common")
    op.drop_column("discord_config", "attendance_excuse_if_unavailable", schema="common")
    op.drop_column("raid_attendance", "raid_helper_status", schema="patt")
    op.drop_column("raid_attendance", "was_available", schema="patt")
    op.drop_column("raid_events", "signup_snapshot_at", schema="patt")
