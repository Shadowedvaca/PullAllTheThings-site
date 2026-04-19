"""feat: u.gg URL overhaul — drop hero-slug targets, deactivate overall source

Old u.gg URLs embedded a ?hero= slug and ?role= param per hero talent.
New URLs are path-based (/gear/raid, /gear) with one target per spec — no HT split.
- Deactivate the u.gg Overall source (no overall page exists on u.gg).
- Delete all existing u.gg scrape targets so discover_targets re-generates
  them with the new URL format and hero_talent_id=NULL.
- bis_entries rows referencing those targets are cleaned up via CASCADE or
  will be rebuilt on the next Sync BIS Lists run.
"""

revision = "0147"
down_revision = "0146"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # Deactivate the u.gg Overall source — u.gg has no overall gear page.
    op.execute("""
        UPDATE ref.bis_list_sources
           SET is_active = FALSE
         WHERE origin = 'ugg'
           AND content_type = 'overall'
    """)

    # Delete all existing u.gg scrape targets (old hero-slug URLs).
    # landing.bis_scrape_raw rows reference these via target_id FK — delete those first.
    op.execute("""
        DELETE FROM landing.bis_scrape_raw
         WHERE target_id IN (
             SELECT bst.id
               FROM config.bis_scrape_targets bst
               JOIN ref.bis_list_sources src ON src.id = bst.source_id
              WHERE src.origin = 'ugg'
         )
    """)

    op.execute("""
        DELETE FROM config.bis_scrape_targets
         WHERE source_id IN (
             SELECT id FROM ref.bis_list_sources WHERE origin = 'ugg'
         )
    """)

    # Clear u.gg BIS entries — they will be repopulated after the next scrape + rebuild.
    op.execute("""
        DELETE FROM enrichment.bis_entries
         WHERE source_id IN (
             SELECT id FROM ref.bis_list_sources WHERE origin = 'ugg'
         )
    """)


def downgrade() -> None:
    # Re-activating the overall source and restoring old targets is not worth
    # automating — re-run discover_targets with old code if a rollback is needed.
    op.execute("""
        UPDATE ref.bis_list_sources
           SET is_active = TRUE
         WHERE origin = 'ugg'
           AND content_type = 'overall'
    """)
