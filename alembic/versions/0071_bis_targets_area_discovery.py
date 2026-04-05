"""BIS targets: area_label column + URL-based unique key + Icy Veins discovery model

Revision ID: 0071
Revises: 0070
Create Date: 2026-04-05

- Add area_label TEXT to bis_scrape_targets (stores discovered tab text)
- Drop 4-col unique (source, spec, hero_talent, content_type) — too rigid for
  Icy Veins where the same spec has variable areas per hero talent / content type
- Add 3-col unique (source, spec, url) — the actual URL is the stable identity
- Deactivate Wowhead Raid + Wowhead M+ sources (Wowhead has only one BIS page
  per spec with no raid/M+ split)
- Clear stale targets/log — re-run Discover URLs + Discover IV Areas after deploy
"""

from alembic import op
import sqlalchemy as sa

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add area_label to store discovered tab/section text
    op.add_column(
        "bis_scrape_targets",
        sa.Column("area_label", sa.Text(), nullable=True),
        schema="guild_identity",
    )

    # Clear stale targets and log FIRST — must happen before constraint change
    # because existing rows may have duplicate (source_id, spec_id, url) values
    # (e.g. multiple hero_talent_id rows sharing the same Wowhead URL).
    op.execute("DELETE FROM guild_identity.bis_scrape_log")
    op.execute("DELETE FROM guild_identity.bis_scrape_targets")

    # Drop the old 4-column unique constraint
    # PostgreSQL auto-names inline UNIQUE as table_col1_col2_..._key
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'bis_scrape_targets_source_id_spec_id_hero_talent_id_content_typ_key'
            ) THEN
                ALTER TABLE guild_identity.bis_scrape_targets
                DROP CONSTRAINT bis_scrape_targets_source_id_spec_id_hero_talent_id_content_typ_key;
            END IF;
        END $$;
    """)

    # Add new URL-based unique constraint (table is now empty — no conflicts)
    op.execute("""
        ALTER TABLE guild_identity.bis_scrape_targets
        ADD CONSTRAINT uq_bis_scrape_targets_source_spec_url
        UNIQUE (source_id, spec_id, url)
    """)

    # Wowhead has only one BIS page per spec — deactivate Raid and M+ sources
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET is_active = FALSE
         WHERE name IN ('Wowhead Raid', 'Wowhead M+')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM guild_identity.bis_scrape_log")
    op.execute("DELETE FROM guild_identity.bis_scrape_targets")

    op.execute("""
        ALTER TABLE guild_identity.bis_scrape_targets
        DROP CONSTRAINT IF EXISTS uq_bis_scrape_targets_source_spec_url
    """)
    op.execute("""
        ALTER TABLE guild_identity.bis_scrape_targets
        ADD CONSTRAINT bis_scrape_targets_source_id_spec_id_hero_talent_id_content_typ_key
        UNIQUE (source_id, spec_id, hero_talent_id, content_type)
    """)

    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET is_active = TRUE
         WHERE name IN ('Wowhead Raid', 'Wowhead M+')
    """)

    op.drop_column("bis_scrape_targets", "area_label", schema="guild_identity")
