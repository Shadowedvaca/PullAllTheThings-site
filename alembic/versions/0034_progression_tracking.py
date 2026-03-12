"""Phase 4.3: Progression tracking — raid, M+, achievements, snapshots

Revision ID: 0034
Revises: 0033
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

# Placeholder achievement IDs — admin must update these when a new tier launches.
# Find current IDs on Wowhead: search for "Ahead of the Curve" etc.
TRACKED_ACHIEVEMENTS_SEED = [
    (40681, "Ahead of the Curve: Queen Ansurek", "raid"),
    (40682, "Cutting Edge: Queen Ansurek", "raid"),
    (19352, "Ahead of the Curve (placeholder)", "raid"),
    (19353, "Cutting Edge (placeholder)", "raid"),
    (40524, "Keystone Master: Season One", "mythic_plus"),
    (40525, "Keystone Hero: Season One", "mythic_plus"),
    (40399, "Keystone Conqueror: Season One", "mythic_plus"),
]


def upgrade():
    # ── New tables in guild_identity ────────────────────────────────────────

    op.create_table(
        "character_raid_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id", sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raid_name", sa.String(100), nullable=False),
        sa.Column("raid_id", sa.Integer, nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("boss_name", sa.String(100), nullable=False),
        sa.Column("boss_id", sa.Integer, nullable=False),
        sa.Column("kill_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("character_id", "boss_id", "difficulty",
                            name="uq_raid_progress_char_boss_diff"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_raid_progress_char",
        "character_raid_progress",
        ["character_id"],
        schema="guild_identity",
    )

    op.create_table(
        "character_mythic_plus",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id", sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season_id", sa.Integer, nullable=False),
        sa.Column("overall_rating", sa.Numeric(7, 1), server_default="0"),
        sa.Column("dungeon_name", sa.String(100), nullable=False),
        sa.Column("dungeon_id", sa.Integer, nullable=False),
        sa.Column("best_level", sa.Integer, server_default="0"),
        sa.Column("best_timed", sa.Boolean, server_default="false"),
        sa.Column("best_score", sa.Numeric(7, 1), server_default="0"),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("character_id", "season_id", "dungeon_id",
                            name="uq_mplus_char_season_dungeon"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_mplus_char",
        "character_mythic_plus",
        ["character_id"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_mplus_season",
        "character_mythic_plus",
        ["season_id"],
        schema="guild_identity",
    )

    op.create_table(
        "tracked_achievements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("achievement_id", sa.Integer, nullable=False, unique=True),
        sa.Column("achievement_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(50), server_default="general"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        schema="guild_identity",
    )

    op.create_table(
        "character_achievements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id", sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("achievement_id", sa.Integer, nullable=False),
        sa.Column("achievement_name", sa.String(200), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("character_id", "achievement_id",
                            name="uq_char_achievement"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_achievements_char",
        "character_achievements",
        ["character_id"],
        schema="guild_identity",
    )

    op.create_table(
        "progression_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id", sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("raid_kills_json", JSONB, nullable=True),
        sa.Column("mythic_rating", sa.Numeric(7, 1), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("character_id", "snapshot_date",
                            name="uq_snapshot_char_date"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_snapshots_date",
        "progression_snapshots",
        ["snapshot_date"],
        schema="guild_identity",
    )

    # ── Alter wow_characters ─────────────────────────────────────────────────
    op.add_column(
        "wow_characters",
        sa.Column("last_progression_sync", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "wow_characters",
        sa.Column("last_profession_sync", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="guild_identity",
    )

    # ── Alter site_config ────────────────────────────────────────────────────
    op.add_column(
        "site_config",
        sa.Column("current_mplus_season_id", sa.Integer, nullable=True),
        schema="common",
    )

    # ── Add progression screen_permission ───────────────────────────────────
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO common.screen_permissions
                (screen_key, display_name, url_path, category, category_label,
                 category_order, nav_order, min_rank_level)
            VALUES
                ('progression', 'Progression', '/admin/progression',
                 'player_management', 'Player Management', 0, 5, 4)
            ON CONFLICT (screen_key) DO NOTHING
        """)
    )

    # ── Seed tracked_achievements ────────────────────────────────────────────
    conn.execute(
        sa.text("""
            INSERT INTO guild_identity.tracked_achievements
                (achievement_id, achievement_name, category)
            VALUES
                (:aid, :aname, :cat)
            ON CONFLICT (achievement_id) DO NOTHING
        """),
        [
            {"aid": aid, "aname": name, "cat": cat}
            for aid, name, cat in TRACKED_ACHIEVEMENTS_SEED
        ],
    )


def downgrade():
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'progression'")
    op.drop_column("site_config", "current_mplus_season_id", schema="common")
    op.drop_column("wow_characters", "last_profession_sync", schema="guild_identity")
    op.drop_column("wow_characters", "last_progression_sync", schema="guild_identity")
    op.drop_index("idx_snapshots_date", "progression_snapshots", schema="guild_identity")
    op.drop_table("progression_snapshots", schema="guild_identity")
    op.drop_index("idx_achievements_char", "character_achievements", schema="guild_identity")
    op.drop_table("character_achievements", schema="guild_identity")
    op.drop_table("tracked_achievements", schema="guild_identity")
    op.drop_index("idx_mplus_season", "character_mythic_plus", schema="guild_identity")
    op.drop_index("idx_mplus_char", "character_mythic_plus", schema="guild_identity")
    op.drop_table("character_mythic_plus", schema="guild_identity")
    op.drop_index("idx_raid_progress_char", "character_raid_progress", schema="guild_identity")
    op.drop_table("character_raid_progress", schema="guild_identity")
