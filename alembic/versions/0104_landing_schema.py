"""Phase A — Create landing, enrichment, and viz schemas with landing tables.

landing schema: raw API payloads, one table per source, insert-only.
enrichment schema: structured facts derived by stored procedures (tables created here,
  sprocs added in Phase B).
viz schema: views only (created here as empty schema; views added in Phase C).

Revision ID: 0104
Revises: 0103
"""

from alembic import op

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS landing")
    op.execute("CREATE SCHEMA IF NOT EXISTS enrichment")
    op.execute("CREATE SCHEMA IF NOT EXISTS viz")

    # -------------------------------------------------------------------------
    # landing.blizzard_journal_encounters
    # Raw response from GET /data/wow/journal-encounter/{encounter_id}
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.blizzard_journal_encounters (
            id              SERIAL PRIMARY KEY,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            encounter_id    INTEGER NOT NULL,
            instance_id     INTEGER NOT NULL,
            payload         JSONB NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_bje_encounter
            ON landing.blizzard_journal_encounters (encounter_id)
    """)

    # -------------------------------------------------------------------------
    # landing.blizzard_items
    # Raw response from GET /data/wow/item/{item_id}
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.blizzard_items (
            id               SERIAL PRIMARY KEY,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            blizzard_item_id INTEGER NOT NULL,
            payload          JSONB NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_bi_item
            ON landing.blizzard_items (blizzard_item_id)
    """)

    # -------------------------------------------------------------------------
    # landing.wowhead_tooltips
    # Raw tooltip JSON from https://nether.wowhead.com/tooltip/item/{item_id}
    # The full JSON response is stored (not just the tooltip HTML field).
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.wowhead_tooltips (
            id               SERIAL PRIMARY KEY,
            fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            blizzard_item_id INTEGER NOT NULL,
            payload          JSONB NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_wt_item
            ON landing.wowhead_tooltips (blizzard_item_id)
    """)

    # -------------------------------------------------------------------------
    # landing.blizzard_appearances
    # Raw response from GET /data/wow/item-appearance/{appearance_id}
    # Used for catalyst item discovery.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.blizzard_appearances (
            id              SERIAL PRIMARY KEY,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            appearance_id   INTEGER NOT NULL,
            payload         JSONB NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_ba_appearance
            ON landing.blizzard_appearances (appearance_id)
    """)

    # -------------------------------------------------------------------------
    # landing.bis_scrape_raw
    # Raw HTML/JSON fetched from BIS sites (Wowhead, u.gg, Icy Veins).
    # source: 'wowhead', 'archon', 'icy_veins' — matches bis_list_sources.origin
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.bis_scrape_raw (
            id          SERIAL PRIMARY KEY,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source      VARCHAR(50) NOT NULL,
            url         TEXT NOT NULL,
            content     TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_bsr_source_url
            ON landing.bis_scrape_raw (source, url)
    """)


def downgrade():
    op.execute("DROP SCHEMA IF EXISTS viz CASCADE")
    op.execute("DROP SCHEMA IF EXISTS enrichment CASCADE")
    op.execute("DROP SCHEMA IF EXISTS landing CASCADE")
