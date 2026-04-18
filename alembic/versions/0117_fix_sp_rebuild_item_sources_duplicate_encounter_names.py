"""Fix sp_rebuild_item_sources: handle duplicate encounter names.

Blizzard's journal API sometimes assigns two different encounter IDs to the
same encounter name within the same instance (e.g. encounter 91 and 2622 are
both named 'Foe Reaper 5000' in Deadmines).  The outer SELECT DISTINCT did not
collapse these because blizzard_encounter_id differs, causing a unique-constraint
violation on (blizzard_item_id, instance_type, encounter_name) at insert time.

Fix: add ON CONFLICT (blizzard_item_id, instance_type, encounter_name) DO NOTHING
so that when two encounter IDs share a name we keep the first row and silently
skip the duplicate.

Also removes the now-redundant DISTINCT ON inner subqueries for encounters and
instances — both tables have a UNIQUE constraint on their ID column (migration
0115) so each ID appears exactly once; the subqueries collapse to plain SELECT.

Revision ID: 0117
Revises: 0116
Create Date: 2026-04-16
"""

from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_sources()
        LANGUAGE plpgsql
        AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.item_sources;

            INSERT INTO enrichment.item_sources (
                blizzard_item_id,
                instance_type,
                encounter_name,
                instance_name,
                blizzard_instance_id,
                blizzard_encounter_id,
                quality_tracks,
                is_junk
            )
            SELECT
                (item_entry->'item'->>'id')::int            AS blizzard_item_id,
                i.instance_type,
                e.payload->>'name'                          AS encounter_name,
                i.instance_name,
                i.instance_id                               AS blizzard_instance_id,
                e.encounter_id                              AS blizzard_encounter_id,
                enrichment._quality_tracks(i.instance_type) AS quality_tracks,
                FALSE                                       AS is_junk
            FROM landing.blizzard_journal_encounters e
            JOIN landing.blizzard_journal_instances  i ON i.instance_id = e.instance_id
            CROSS JOIN LATERAL jsonb_array_elements(e.payload->'items') AS item_entry
            WHERE (item_entry->'item'->>'id')::int IN (
                SELECT blizzard_item_id FROM enrichment.items
            )
            ON CONFLICT (blizzard_item_id, instance_type, encounter_name) DO NOTHING;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_sources: % rows inserted', v_count;
        END;
        $$
    """)


def downgrade() -> None:
    # Restore previous version (with DISTINCT ON subqueries, without ON CONFLICT)
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_sources()
        LANGUAGE plpgsql
        AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.item_sources;

            INSERT INTO enrichment.item_sources (
                blizzard_item_id,
                instance_type,
                encounter_name,
                instance_name,
                blizzard_instance_id,
                blizzard_encounter_id,
                quality_tracks,
                is_junk
            )
            SELECT DISTINCT
                (item_entry->'item'->>'id')::int            AS blizzard_item_id,
                i.instance_type,
                e.payload->>'name'                           AS encounter_name,
                i.instance_name,
                i.instance_id                                AS blizzard_instance_id,
                e.encounter_id                               AS blizzard_encounter_id,
                enrichment._quality_tracks(i.instance_type) AS quality_tracks,
                FALSE                                        AS is_junk
            FROM (
                SELECT DISTINCT ON (encounter_id)
                    encounter_id, instance_id, payload
                FROM landing.blizzard_journal_encounters
                ORDER BY encounter_id, fetched_at DESC
            ) e
            JOIN (
                SELECT DISTINCT ON (instance_id)
                    instance_id, instance_name, instance_type
                FROM landing.blizzard_journal_instances
                ORDER BY instance_id, fetched_at DESC
            ) i ON i.instance_id = e.instance_id
            CROSS JOIN LATERAL jsonb_array_elements(e.payload->'items') AS item_entry
            WHERE (item_entry->'item'->>'id')::int IN (
                SELECT blizzard_item_id FROM enrichment.items
            );

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_sources: % rows inserted', v_count;
        END;
        $$
    """)
