"""item_sources — clean schema: rename columns, drop quality_tracks, fix type values.

Revision ID: 0082
Revises: 0081
"""

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade():
    # Use a single DO block so every step is idempotent — safe to re-run if a
    # prior attempt partially applied.
    op.execute("""
    DO $$
    BEGIN

        -- 1. Drop old constraints (IF EXISTS handles partial-apply case)
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS uq_item_source;
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS item_sources_source_type_check;
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS item_sources_instance_type_check;

        -- 2. Rename columns (skip if already renamed)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'source_type'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN source_type TO instance_type;
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'source_name'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN source_name TO encounter_name;
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'source_instance'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN source_instance TO instance_name;
        END IF;

        -- 3. Drop quality_tracks (skip if already gone)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'quality_tracks'
        ) THEN
            ALTER TABLE guild_identity.item_sources DROP COLUMN quality_tracks;
        END IF;

        -- 4. Migrate instance_type values (CHECK constraint already dropped above)
        --    World boss rows: any row whose instance_name indicates it was a world boss.
        UPDATE guild_identity.item_sources
           SET instance_type = 'world_boss'
         WHERE instance_type IN ('raid_boss', 'raid')
           AND instance_name IN ('World Boss', 'Midnight');

        --    Remaining 'raid_boss' rows are regular raid encounters.
        UPDATE guild_identity.item_sources
           SET instance_type = 'raid'
         WHERE instance_type = 'raid_boss';

        -- 5. Add clean CHECK constraint
        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT item_sources_instance_type_check
            CHECK (instance_type IN ('raid', 'dungeon', 'world_boss'));

        -- 6. Recreate unique constraint on new column names
        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT uq_item_source
            UNIQUE (item_id, instance_type, encounter_name);

    END $$;
    """)


def downgrade():
    op.execute("""
    DO $$
    BEGIN
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS uq_item_source;
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS item_sources_instance_type_check;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'instance_type'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN instance_type TO source_type;
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'encounter_name'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN encounter_name TO source_name;
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'guild_identity'
               AND table_name   = 'item_sources'
               AND column_name  = 'instance_name'
        ) THEN
            ALTER TABLE guild_identity.item_sources
                RENAME COLUMN instance_name TO source_instance;
        END IF;

        UPDATE guild_identity.item_sources
           SET source_type = 'raid_boss'
         WHERE source_type IN ('raid', 'world_boss');

        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT item_sources_source_type_check
            CHECK (source_type IN ('raid_boss', 'dungeon'));

        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT uq_item_source
            UNIQUE (item_id, source_type, source_name);
    END $$;
    """)
