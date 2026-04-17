"""feat: Phase G — drop guild_identity.bis_list_entries and trinket_tier_ratings

Phase G of the gear plan schema overhaul. All reads and writes to these two tables
have been switched to their enrichment equivalents:
  - guild_identity.bis_list_entries    → enrichment.bis_entries
  - guild_identity.trinket_tier_ratings → enrichment.trinket_ratings

Files updated:
  - bis_sync.py: removed _upsert_bis_entries, _upsert_trinket_ratings; cross_reference() uses enrichment
  - item_source_sync.py: EXISTS subqueries use enrichment.bis_entries
  - bis_routes.py: /entries CRUD, /trinket-ratings-status, p3_total count use enrichment
  - gear_plan_auto_setup.py: auto_setup_gear_plan() uses enrichment.bis_entries
  - gear_plan_service.py: populate_from_bis() uses enrichment.bis_entries
  - item_service.py: enrich_blizzard_metadata() uses enrichment.bis_entries
  - models.py: BisListEntry ORM class removed

Revision ID: 0131
Revises: 0130
Create Date: 2026-04-17
"""
from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE IF EXISTS guild_identity.bis_list_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS guild_identity.trinket_tier_ratings CASCADE")


def downgrade():
    op.execute("""
        CREATE TABLE guild_identity.bis_list_entries (
            id              SERIAL PRIMARY KEY,
            source_id       INTEGER NOT NULL REFERENCES ref.bis_list_sources(id) ON DELETE CASCADE,
            spec_id         INTEGER NOT NULL REFERENCES ref.specializations(id) ON DELETE CASCADE,
            hero_talent_id  INTEGER REFERENCES ref.hero_talents(id) ON DELETE SET NULL,
            slot            VARCHAR(20) NOT NULL,
            item_id         INTEGER NOT NULL REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
            priority        INTEGER NOT NULL DEFAULT 1,
            notes           TEXT,
            UNIQUE (source_id, spec_id, hero_talent_id, slot, item_id)
        )
    """)
    op.execute("""
        CREATE TABLE guild_identity.trinket_tier_ratings (
            id              SERIAL PRIMARY KEY,
            source_id       INTEGER NOT NULL REFERENCES ref.bis_list_sources(id) ON DELETE CASCADE,
            spec_id         INTEGER NOT NULL REFERENCES ref.specializations(id) ON DELETE CASCADE,
            hero_talent_id  INTEGER REFERENCES ref.hero_talents(id) ON DELETE SET NULL,
            item_id         INTEGER NOT NULL REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
            tier            VARCHAR(2) NOT NULL CHECK (tier IN ('S','A','B','C','D')),
            sort_order      INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (source_id, spec_id, hero_talent_id, item_id)
        )
    """)
