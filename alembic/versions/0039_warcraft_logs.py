"""Phase 4.5: Warcraft Logs Integration

Revision ID: 0039
Revises: 0038
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    # ── guild_identity.wcl_config — single-row config ─────────────────────
    op.create_table(
        "wcl_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.String(100)),
        sa.Column("client_secret_encrypted", sa.String(500)),
        sa.Column("wcl_guild_name", sa.String(100)),
        sa.Column("wcl_server_slug", sa.String(50)),
        sa.Column("wcl_server_region", sa.String(5), server_default="us"),
        sa.Column(
            "is_configured", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("last_sync", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_sync_status", sa.String(20)),
        sa.Column("last_sync_error", sa.Text),
        sa.Column(
            "sync_enabled", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="guild_identity",
    )
    # Insert the single config row
    op.execute("INSERT INTO guild_identity.wcl_config DEFAULT VALUES")

    # ── guild_identity.character_parses ───────────────────────────────────
    op.create_table(
        "character_parses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encounter_id", sa.Integer, nullable=False),
        sa.Column("encounter_name", sa.String(100), nullable=False),
        sa.Column("zone_id", sa.Integer, nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("difficulty", sa.Integer, nullable=False),
        sa.Column("spec", sa.String(50), nullable=False),
        sa.Column("percentile", sa.Numeric(5, 1), nullable=False),
        sa.Column("amount", sa.Numeric(12, 1)),
        sa.Column("report_code", sa.String(20)),
        sa.Column("fight_id", sa.Integer),
        sa.Column("fight_date", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "character_id",
            "encounter_id",
            "difficulty",
            "spec",
            name="uq_parse_char_enc_diff_spec",
        ),
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_parses_char ON guild_identity.character_parses(character_id)"
    )
    op.execute(
        "CREATE INDEX idx_parses_zone ON guild_identity.character_parses(zone_id)"
    )
    op.execute(
        "CREATE INDEX idx_parses_pct ON guild_identity.character_parses(percentile DESC)"
    )

    # ── guild_identity.raid_reports ───────────────────────────────────────
    op.create_table(
        "raid_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("report_code", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(200)),
        sa.Column("raid_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("zone_id", sa.Integer),
        sa.Column("zone_name", sa.String(100)),
        sa.Column("owner_name", sa.String(50)),
        sa.Column("boss_kills", sa.Integer, server_default="0"),
        sa.Column("wipes", sa.Integer, server_default="0"),
        sa.Column("duration_ms", sa.BigInteger),
        sa.Column(
            "attendees",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("report_url", sa.String(255)),
        sa.Column(
            "last_synced",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_reports_date ON guild_identity.raid_reports(raid_date DESC)"
    )
    op.execute(
        "CREATE INDEX idx_reports_zone ON guild_identity.raid_reports(zone_id)"
    )

    # ── screen_permission for warcraft_logs ───────────────────────────────
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO common.screen_permissions
                (screen_key, display_name, url_path, category, category_label,
                 category_order, nav_order, min_rank_level)
            VALUES
                ('warcraft_logs', 'Warcraft Logs', '/admin/warcraft-logs',
                 'player_management', 'Player Management', 0, 6, 4)
            ON CONFLICT (screen_key) DO NOTHING
        """)
    )


def downgrade():
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'warcraft_logs'"
    )
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_reports_zone")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_reports_date")
    op.drop_table("raid_reports", schema="guild_identity")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_parses_pct")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_parses_zone")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_parses_char")
    op.drop_table("character_parses", schema="guild_identity")
    op.drop_table("wcl_config", schema="guild_identity")
