"""Link bis_list_sources to guide_sites for slug_separator-driven URL building

Revision ID: 0070
Revises: 0069
Create Date: 2026-04-05

Adds guide_site_id FK to guild_identity.bis_list_sources so discover_targets
can read the slug_separator from common.guide_sites and generate class/spec
slugs correctly (death_knight vs death-knight) without hardcoded maps.

Also clears stale scrape targets — re-run Discover URLs after deploy.
"""

from alembic import op
import sqlalchemy as sa

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bis_list_sources",
        sa.Column("guide_site_id", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.create_foreign_key(
        "fk_bis_list_sources_guide_site",
        "bis_list_sources",
        "guide_sites",
        ["guide_site_id"],
        ["id"],
        source_schema="guild_identity",
        referent_schema="common",
        ondelete="SET NULL",
    )

    # Archon = u.gg (id=3), Wowhead = id=1, Icy Veins = id=2
    op.execute("""
        UPDATE guild_identity.bis_list_sources SET guide_site_id = 3 WHERE origin = 'archon'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources SET guide_site_id = 1 WHERE origin = 'wowhead'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources SET guide_site_id = 2 WHERE origin = 'icy_veins'
    """)

    # Clear stale targets so Discover URLs rebuilds with correct slugs
    op.execute("DELETE FROM guild_identity.bis_scrape_log")
    op.execute("DELETE FROM guild_identity.bis_scrape_targets")


def downgrade() -> None:
    op.execute("DELETE FROM guild_identity.bis_scrape_log")
    op.execute("DELETE FROM guild_identity.bis_scrape_targets")
    op.drop_constraint(
        "fk_bis_list_sources_guide_site",
        "bis_list_sources",
        schema="guild_identity",
        type_="foreignkey",
    )
    op.drop_column("bis_list_sources", "guide_site_id", schema="guild_identity")
