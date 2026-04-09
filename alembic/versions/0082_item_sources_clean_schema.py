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
    # ── 1. Drop the old unique constraint (references old column names) ──────
    op.execute("ALTER TABLE guild_identity.item_sources DROP CONSTRAINT IF EXISTS uq_item_source")

    # ── 2. Rename columns to clean names ────────────────────────────────────
    op.alter_column("item_sources", "source_type",     new_column_name="instance_type", schema="guild_identity")
    op.alter_column("item_sources", "source_name",     new_column_name="encounter_name", schema="guild_identity")
    op.alter_column("item_sources", "source_instance", new_column_name="instance_name",  schema="guild_identity")

    # ── 3. Drop quality_tracks — now derived from source_config at read time ─
    op.drop_column("item_sources", "quality_tracks", schema="guild_identity")

    # ── 4. Migrate instance_type values ─────────────────────────────────────
    # Classify known world boss rows first (instance_name matches expansion name
    # or was already renamed to 'World Boss' by a previous partial fix).
    op.execute("""
        UPDATE guild_identity.item_sources
           SET instance_type = 'world_boss'
         WHERE instance_type = 'raid_boss'
           AND instance_name IN ('World Boss', 'Midnight')
    """)
    # Remaining 'raid_boss' rows are regular raid encounters.
    op.execute("""
        UPDATE guild_identity.item_sources
           SET instance_type = 'raid'
         WHERE instance_type = 'raid_boss'
    """)

    # ── 5. Drop old CHECK constraint and add clean one ───────────────────────
    op.execute("""
        ALTER TABLE guild_identity.item_sources
        DROP CONSTRAINT IF EXISTS item_sources_source_type_check
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_sources
        ADD CONSTRAINT item_sources_instance_type_check
        CHECK (instance_type IN ('raid', 'dungeon', 'world_boss'))
    """)

    # ── 6. Recreate unique constraint on new column names ───────────────────
    op.create_unique_constraint(
        "uq_item_source",
        "item_sources",
        ["item_id", "instance_type", "encounter_name"],
        schema="guild_identity",
    )


def downgrade():
    op.drop_constraint("uq_item_source", "item_sources", schema="guild_identity")
    op.execute("ALTER TABLE guild_identity.item_sources DROP CONSTRAINT IF EXISTS item_sources_instance_type_check")

    op.alter_column("item_sources", "instance_type",  new_column_name="source_type",    schema="guild_identity")
    op.alter_column("item_sources", "encounter_name", new_column_name="source_name",     schema="guild_identity")
    op.alter_column("item_sources", "instance_name",  new_column_name="source_instance", schema="guild_identity")

    op.execute("UPDATE guild_identity.item_sources SET source_type = 'raid_boss' WHERE source_type IN ('raid', 'world_boss')")
    op.execute("ALTER TABLE guild_identity.item_sources ADD CONSTRAINT item_sources_source_type_check CHECK (source_type IN ('raid_boss', 'dungeon'))")

    import sqlalchemy as sa
    from sqlalchemy.dialects import postgresql
    op.add_column("item_sources", sa.Column("quality_tracks", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False), schema="guild_identity")
    op.create_unique_constraint("uq_item_source", "item_sources", ["item_id", "source_type", "source_name"], schema="guild_identity")
