"""Add guild_identity.trinket_tier_ratings — Phase 1F.

Stores per-item trinket tier ratings (S/A/B/C/D) scraped from BIS guide
sources (initially Wowhead). Ratings are per-spec and optionally per-hero-talent;
hero_talent_id=NULL means "applies to all hero talents for this spec."

Content type is NOT stored here — it is derived at query time by joining
item_sources on item_id.

Revision ID: 0100
Revises: 0099
"""

from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE guild_identity.trinket_tier_ratings (
            id              SERIAL PRIMARY KEY,

            source_id       INTEGER NOT NULL
                            REFERENCES guild_identity.bis_list_sources(id)
                            ON DELETE RESTRICT,
            spec_id         INTEGER NOT NULL
                            REFERENCES guild_identity.specializations(id)
                            ON DELETE RESTRICT,
            hero_talent_id  INTEGER
                            REFERENCES guild_identity.hero_talents(id)
                            ON DELETE SET NULL,
            item_id         INTEGER NOT NULL
                            REFERENCES guild_identity.wow_items(id)
                            ON DELETE RESTRICT,

            tier            VARCHAR(2) NOT NULL
                            CHECK (tier IN ('S', 'A', 'B', 'C', 'D', 'F')),
            sort_order      INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

            UNIQUE (source_id, spec_id, hero_talent_id, item_id)
        )
    """)

    op.execute("""
        CREATE INDEX idx_trinket_ratings_spec_source
            ON guild_identity.trinket_tier_ratings (spec_id, source_id)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS guild_identity.trinket_tier_ratings")
