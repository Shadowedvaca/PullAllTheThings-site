"""feat: Phase B — backfill landing.wowhead_tooltips from wow_items tooltip HTML

Populates landing.wowhead_tooltips for every wow_items row that has
wowhead_tooltip_html set but has no matching row in landing.wowhead_tooltips.
Wraps the raw HTML in {"tooltip": "..."} so the payload shape matches what
item_service.py writes during live enrichment runs.

After this migration, item_source_sync.py can read tooltip HTML from
landing.wowhead_tooltips instead of guild_identity.wow_items, breaking one
more Python dependency on the wow_items table.

This is Phase B of the wow_items retirement plan
(reference/gear-plan-1.0-wow_items-fix.md).

Revision ID: 0142
Revises: 0141
"""

revision = "0142"
down_revision = "0141"

from alembic import op


def upgrade():
    op.execute("""
        INSERT INTO landing.wowhead_tooltips (blizzard_item_id, payload)
        SELECT wi.blizzard_item_id,
               jsonb_build_object('tooltip', wi.wowhead_tooltip_html)
          FROM guild_identity.wow_items wi
         WHERE wi.wowhead_tooltip_html IS NOT NULL
           AND wi.wowhead_tooltip_html != ''
           AND NOT EXISTS (
               SELECT 1 FROM landing.wowhead_tooltips wt
                WHERE wt.blizzard_item_id = wi.blizzard_item_id
           )
    """)


def downgrade():
    pass
