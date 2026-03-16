"""Phase 4.8: Quotes 2.0 — per-player quote subjects.

Revision ID: 0044
Revises: 0043
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    # ── patt.quote_subjects ────────────────────────────────────────────────
    op.create_table(
        "quote_subjects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "player_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_slug", sa.String(32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "command_slug ~ '^[a-z][a-z0-9_-]{0,30}$'",
            name="quote_subjects_slug_format",
        ),
        schema="patt",
    )
    op.execute(
        "CREATE UNIQUE INDEX quote_subjects_player_id_idx ON patt.quote_subjects(player_id)"
    )

    # ── Add subject_id FK to guild_quotes ──────────────────────────────────
    op.add_column(
        "guild_quotes",
        sa.Column(
            "subject_id",
            sa.Integer,
            sa.ForeignKey("patt.quote_subjects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema="patt",
    )
    op.execute(
        "CREATE INDEX guild_quotes_subject_id_idx ON patt.guild_quotes(subject_id)"
    )

    # ── Add subject_id FK to guild_quote_titles ────────────────────────────
    op.add_column(
        "guild_quote_titles",
        sa.Column(
            "subject_id",
            sa.Integer,
            sa.ForeignKey("patt.quote_subjects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema="patt",
    )
    op.execute(
        "CREATE INDEX guild_quote_titles_subject_id_idx ON patt.guild_quote_titles(subject_id)"
    )

    # ── Data migration: assign existing quotes/titles to a "mito" subject ──
    # Only runs if guild_quotes has rows.  If no player named "Mito" is found,
    # the subject row is still created so quotes aren't orphaned.
    op.execute("""
        DO $$
        DECLARE
            quote_count INT;
            mito_player_id INT;
            subject_id INT;
        BEGIN
            SELECT COUNT(*) INTO quote_count FROM patt.guild_quotes;
            IF quote_count = 0 THEN
                RETURN;
            END IF;

            -- Look for a player whose display_name (discord username) matches 'Mito'
            SELECT p.id INTO mito_player_id
            FROM guild_identity.players p
            WHERE LOWER(p.display_name) = 'mito'
            LIMIT 1;

            -- Also try discord_users display_name as fallback
            IF mito_player_id IS NULL THEN
                SELECT p.id INTO mito_player_id
                FROM guild_identity.players p
                JOIN guild_identity.discord_users du ON du.player_id = p.id
                WHERE LOWER(du.display_name) = 'mito'
                LIMIT 1;
            END IF;

            -- We need a player_id; if still NULL leave subject_id NULL on quotes
            -- (admin will see a banner and can assign via UI)
            IF mito_player_id IS NULL THEN
                RETURN;
            END IF;

            INSERT INTO patt.quote_subjects (player_id, command_slug, display_name)
            VALUES (mito_player_id, 'mito', 'Mito')
            ON CONFLICT (player_id) DO NOTHING
            RETURNING id INTO subject_id;

            IF subject_id IS NULL THEN
                SELECT id INTO subject_id FROM patt.quote_subjects WHERE player_id = mito_player_id;
            END IF;

            UPDATE patt.guild_quotes SET subject_id = subject_id WHERE subject_id IS NULL;
            UPDATE patt.guild_quote_titles SET subject_id = subject_id WHERE subject_id IS NULL;
        END
        $$;
    """)

    # ── screen_permissions — quotes ────────────────────────────────────────
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('quotes', 'Guild Quotes', '/admin/quotes',
             'social_tools', 'Social Tools', 4, 2, 4)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'quotes'"
    )
    op.execute("DROP INDEX IF EXISTS patt.guild_quote_titles_subject_id_idx")
    op.drop_column("guild_quote_titles", "subject_id", schema="patt")
    op.execute("DROP INDEX IF EXISTS patt.guild_quotes_subject_id_idx")
    op.drop_column("guild_quotes", "subject_id", schema="patt")
    op.execute("DROP INDEX IF EXISTS patt.quote_subjects_player_id_idx")
    op.drop_table("quote_subjects", schema="patt")
