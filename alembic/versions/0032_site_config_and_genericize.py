"""Phase 4.0: site_config table, rank_wow_mapping, and rename mito tables

Revision ID: 0032
Revises: 0031
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    # -------------------------------------------------------------------------
    # common.site_config — single-row guild configuration
    # -------------------------------------------------------------------------
    op.create_table(
        "site_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_name", sa.String(100), nullable=False, server_default="My Guild"),
        sa.Column("guild_tagline", sa.String(255), nullable=True),
        sa.Column("guild_mission", sa.Text(), nullable=True),
        sa.Column("discord_invite_url", sa.String(255), nullable=True),
        sa.Column("accent_color_hex", sa.String(7), nullable=False, server_default="#d4a84b"),
        sa.Column("realm_display_name", sa.String(50), nullable=True),
        sa.Column("home_realm_slug", sa.String(50), nullable=True),
        sa.Column("guild_name_slug", sa.String(100), nullable=True),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("enable_guild_quotes", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("enable_contests", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("setup_complete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="common",
    )

    # -------------------------------------------------------------------------
    # common.rank_wow_mapping — WoW rank index → platform rank
    # -------------------------------------------------------------------------
    op.create_table(
        "rank_wow_mapping",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wow_rank_index", sa.Integer(), nullable=False),
        sa.Column(
            "guild_rank_id",
            sa.Integer(),
            sa.ForeignKey("common.guild_ranks.id"),
            nullable=False,
        ),
        sa.UniqueConstraint("wow_rank_index"),
        schema="common",
    )

    # -------------------------------------------------------------------------
    # Rename mito tables to guild_quotes / guild_quote_titles
    # -------------------------------------------------------------------------
    op.rename_table("mito_quotes", "guild_quotes", schema="patt")
    op.rename_table("mito_titles", "guild_quote_titles", schema="patt")

    # -------------------------------------------------------------------------
    # Backfill initial guild instance data
    # -------------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO common.site_config (
            guild_name, guild_tagline, guild_mission, discord_invite_url,
            accent_color_hex, realm_display_name, home_realm_slug, guild_name_slug,
            enable_guild_quotes, enable_contests, setup_complete
        ) VALUES (
            'Pull All The Things',
            'Casual Heroic Raiding with Real-Life Balance & Immaculate Vibes',
            'A WoW guild focused on casual heroic raiding with a real-life first philosophy and zero-toxicity culture.',
            'https://discord.gg/jgSSRBvjHM',
            '#d4a84b',
            'Sen''jin',
            'senjin',
            'pull-all-the-things',
            TRUE,
            TRUE,
            TRUE
        )
        """
    )

    op.execute(
        """
        INSERT INTO common.rank_wow_mapping (wow_rank_index, guild_rank_id)
        SELECT v.idx, gr.id
        FROM (VALUES
            (0, 'Guild Leader'),
            (1, 'Officer'),
            (2, 'Veteran'),
            (3, 'Member'),
            (4, 'Initiate')
        ) AS v(idx, rank_name)
        JOIN common.guild_ranks gr ON gr.name = v.rank_name
        ON CONFLICT DO NOTHING
        """
    )

    # -------------------------------------------------------------------------
    # Add site_config screen permission (GL-only, level 5)
    # -------------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('site_config', 'Site Config', '/admin/site-config',
             'admin', 'Administration', 10, 95, 5)
        ON CONFLICT (screen_key) DO NOTHING
        """
    )


def downgrade():
    # Remove screen permission
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'site_config'"
    )

    # Restore mito table names
    op.rename_table("guild_quote_titles", "mito_titles", schema="patt")
    op.rename_table("guild_quotes", "mito_quotes", schema="patt")

    # Drop new tables
    op.drop_table("rank_wow_mapping", schema="common")
    op.drop_table("site_config", schema="common")
