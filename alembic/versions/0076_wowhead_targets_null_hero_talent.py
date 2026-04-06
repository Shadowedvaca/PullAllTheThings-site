"""Set hero_talent_id=NULL for all Wowhead BIS targets and entries

Revision ID: 0076
Revises: 0075
Create Date: 2026-04-06

Wowhead BIS pages are not hero-talent-specific — one page covers all builds
for a spec.  Previously, discover_targets created one target per HT per spec,
but all HTs produced the same URL, so only the first HT's target was ever
inserted (ON CONFLICT DO NOTHING for the rest).  The single inserted target
had hero_talent_id set to whichever HT happened to be first — and entries were
stored under that HT ID, making them invisible when drilling into the other HT.

Fix: set hero_talent_id=NULL on all Wowhead targets and entries so they apply
to all builds.  This matches how Icy Veins targets work.  The list_entries API
already returns NULL-HT entries for any HT query (OR hero_talent_id IS NULL).

Also deletes orphaned per-HT Wowhead target rows that were previously skipped
by ON CONFLICT — they should not exist, but clean up defensively.
"""

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null out hero_talent_id on all Wowhead bis_list_entries
    op.execute("""
        UPDATE guild_identity.bis_list_entries e
           SET hero_talent_id = NULL
          FROM guild_identity.bis_list_sources s
         WHERE e.source_id = s.id
           AND s.origin = 'wowhead'
           AND e.hero_talent_id IS NOT NULL
    """)

    # Null out hero_talent_id on all Wowhead bis_scrape_targets
    op.execute("""
        UPDATE guild_identity.bis_scrape_targets t
           SET hero_talent_id = NULL
          FROM guild_identity.bis_list_sources s
         WHERE t.source_id = s.id
           AND s.origin = 'wowhead'
           AND t.hero_talent_id IS NOT NULL
    """)

    # After nulling, there may be duplicate targets for the same
    # (source_id, spec_id, url) — delete all but the most recent one.
    op.execute("""
        DELETE FROM guild_identity.bis_scrape_targets t
         USING (
             SELECT tt.source_id, tt.spec_id, tt.url,
                    MAX(tt.id) AS keep_id
               FROM guild_identity.bis_scrape_targets tt
               JOIN guild_identity.bis_list_sources s ON s.id = tt.source_id
              WHERE s.origin = 'wowhead'
              GROUP BY tt.source_id, tt.spec_id, tt.url
             HAVING COUNT(*) > 1
         ) dups
         WHERE t.source_id = dups.source_id
           AND t.spec_id   = dups.spec_id
           AND t.url       = dups.url
           AND t.id        < dups.keep_id
    """)


def downgrade() -> None:
    # Downgrade is intentionally a no-op — we cannot recover which HT was
    # originally assigned to each target/entry, and the old behaviour was broken.
    pass
