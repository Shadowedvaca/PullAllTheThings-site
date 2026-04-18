"""feat: Phase E — drop integer FK columns; change tier_token_attrs PK to blizzard_item_id

Drops the legacy wow_items.id surrogate FK columns from all four consumer tables:
  - guild_identity.item_sources         (item_id)
  - guild_identity.character_equipment  (item_id)
  - guild_identity.gear_plan_slots      (desired_item_id)
  - guild_identity.item_recipe_links    (item_id)
  - guild_identity.tier_token_attrs     (token_item_id — was the PK)

For tier_token_attrs, the primary key is swapped from token_item_id (FK→wow_items)
to blizzard_item_id (plain integer, no FK constraint), matching the pattern already
in place on item_recipe_links.blizzard_item_id.

After this migration, guild_identity.wow_items has no inbound FK references and can
be dropped by migration 0146.

Revision ID: 0145
Revises: 0144
"""

revision = "0145"
down_revision = "0144"

from alembic import op


def upgrade():
    # ── item_sources ──────────────────────────────────────────────────────────
    # Safety: mark any rows that somehow still have NULL blizzard_item_id as junk
    # so they are filtered out before we enforce NOT NULL.
    op.execute("""
        UPDATE guild_identity.item_sources
           SET is_suspected_junk = TRUE
         WHERE blizzard_item_id IS NULL
    """)

    op.execute("""
        ALTER TABLE guild_identity.item_sources
            ALTER COLUMN blizzard_item_id SET NOT NULL
    """)

    op.execute("""
        ALTER TABLE guild_identity.item_sources
            DROP COLUMN IF EXISTS item_id
    """)

    # ── character_equipment ───────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE guild_identity.character_equipment
            DROP COLUMN IF EXISTS item_id
    """)

    # ── gear_plan_slots ───────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
            DROP COLUMN IF EXISTS desired_item_id
    """)

    # ── tier_token_attrs — swap PK from token_item_id to blizzard_item_id ─────
    # Drop the old PK (token_item_id FK→wow_items).
    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            DROP CONSTRAINT IF EXISTS tier_token_attrs_pkey
    """)

    # Drop the plain index added in Phase A (PK will create its own unique index).
    op.execute("""
        DROP INDEX IF EXISTS guild_identity.ix_tier_token_attrs_blizzard_item_id
    """)

    # Make blizzard_item_id NOT NULL and promote to PK.
    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            ALTER COLUMN blizzard_item_id SET NOT NULL
    """)

    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            ADD PRIMARY KEY (blizzard_item_id)
    """)

    # Drop the old FK column — also removes the FK constraint to wow_items.
    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            DROP COLUMN IF EXISTS token_item_id
    """)

    # ── item_recipe_links ─────────────────────────────────────────────────────
    # blizzard_item_id is already the key (unique constraint uq_item_recipe_bid).
    # Drop the legacy item_id FK column.
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            DROP COLUMN IF EXISTS item_id
    """)


def downgrade():
    # Downgrade is intentionally minimal — restoring FK columns would require
    # data that is no longer available after the upgrade.
    # The full rollback path is a DB restore from the pre-Phase-E snapshot.
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            ADD COLUMN IF NOT EXISTS item_id INTEGER
    """)

    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            ADD COLUMN IF NOT EXISTS token_item_id INTEGER
    """)

    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
            ADD COLUMN IF NOT EXISTS desired_item_id INTEGER
    """)

    op.execute("""
        ALTER TABLE guild_identity.character_equipment
            ADD COLUMN IF NOT EXISTS item_id INTEGER
    """)

    op.execute("""
        ALTER TABLE guild_identity.item_sources
            ADD COLUMN IF NOT EXISTS item_id INTEGER
    """)
