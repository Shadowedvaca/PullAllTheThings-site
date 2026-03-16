"""Phase 4.7: Voice Channel Attendance Tracking

Revision ID: 0041
Revises: 0040
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    # ── patt.voice_attendance_log ─────────────────────────────────────────
    op.create_table(
        "voice_attendance_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer,
            sa.ForeignKey("patt.raid_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("discord_user_id", sa.String(25), nullable=False),
        sa.Column("channel_id", sa.String(25), nullable=False),
        sa.Column(
            "action",
            sa.String(10),
            sa.CheckConstraint("action IN ('join', 'leave')", name="ck_val_action"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="patt",
    )
    op.execute(
        "CREATE INDEX idx_val_event    ON patt.voice_attendance_log(event_id)"
    )
    op.execute(
        "CREATE INDEX idx_val_user     ON patt.voice_attendance_log(discord_user_id)"
    )
    op.execute(
        "CREATE INDEX idx_val_occurred ON patt.voice_attendance_log(occurred_at)"
    )

    # ── patt.raid_events additions ─────────────────────────────────────────
    op.add_column(
        "raid_events",
        sa.Column("voice_channel_id", sa.String(25), nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_events",
        sa.Column(
            "voice_tracking_enabled",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
        schema="patt",
    )
    op.add_column(
        "raid_events",
        sa.Column("attendance_processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="patt",
    )

    # ── patt.raid_attendance additions ────────────────────────────────────
    op.add_column(
        "raid_attendance",
        sa.Column("minutes_present", sa.SmallInteger, nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("first_join_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("last_leave_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("joined_late", sa.Boolean, nullable=True),
        schema="patt",
    )
    op.add_column(
        "raid_attendance",
        sa.Column("left_early", sa.Boolean, nullable=True),
        schema="patt",
    )

    # ── common.discord_config additions ──────────────────────────────────
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_min_pct", sa.SmallInteger, nullable=False, server_default="75"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_late_grace_min", sa.SmallInteger, nullable=False, server_default="10"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_early_leave_min", sa.SmallInteger, nullable=False, server_default="10"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_trailing_events", sa.SmallInteger, nullable=False, server_default="8"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_habitual_window", sa.SmallInteger, nullable=False, server_default="5"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_habitual_threshold", sa.SmallInteger, nullable=False, server_default="3"
        ),
        schema="common",
    )
    op.add_column(
        "discord_config",
        sa.Column(
            "attendance_feature_enabled", sa.Boolean, nullable=False, server_default="false"
        ),
        schema="common",
    )

    # ── screen_permissions — attendance_report ─────────────────────────
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('attendance_report', 'Attendance', '/admin/attendance',
             'raid_tools', 'Raid Tools', 3, 5, 4)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'attendance_report'"
    )

    for col in [
        "attendance_feature_enabled",
        "attendance_habitual_threshold",
        "attendance_habitual_window",
        "attendance_trailing_events",
        "attendance_early_leave_min",
        "attendance_late_grace_min",
        "attendance_min_pct",
    ]:
        op.drop_column("discord_config", col, schema="common")

    for col in ["left_early", "joined_late", "last_leave_at", "first_join_at", "minutes_present"]:
        op.drop_column("raid_attendance", col, schema="patt")

    for col in ["attendance_processed_at", "voice_tracking_enabled", "voice_channel_id"]:
        op.drop_column("raid_events", col, schema="patt")

    op.execute("DROP INDEX IF EXISTS patt.idx_val_occurred")
    op.execute("DROP INDEX IF EXISTS patt.idx_val_user")
    op.execute("DROP INDEX IF EXISTS patt.idx_val_event")
    op.drop_table("voice_attendance_log", schema="patt")
