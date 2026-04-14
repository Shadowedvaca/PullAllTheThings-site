"""Purge spurious raid/world_boss source rows for craftable items.

Crafted gear in Midnight (e.g. the Arcanoweave set) has Wowhead tooltips that
include /item-set= links pointing to a crafted gear set ID.  enrich_catalyst_
tier_items() Pass 1 uses /item-set= as its heuristic for detecting tier raid
pieces, so it incorrectly inserted raid boss source rows for any craftable item
that (a) is BIS-listed and (b) has an /item-set= tooltip link.

Affected items confirmed on dev: Arcanoweave Cloak (239661, back),
Arcanoweave Bracers (239660, wrist).  Crafted gear never drops from bosses —
these source rows are purely wrong.

The corrected enrich_catalyst_tier_items() (same release) now excludes items in
item_recipe_links from Pass 1 so this cannot recur.

Revision ID: 0102
Revises: 0101
"""

from alembic import op

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DELETE FROM guild_identity.item_sources
         WHERE instance_type IN ('raid', 'world_boss')
           AND item_id IN (
               SELECT item_id FROM guild_identity.item_recipe_links
           )
    """)


def downgrade():
    # Rows are not restored on downgrade.
    pass
