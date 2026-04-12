# Guild Database — Backup & Restore

## Overview

The database is backed up automatically every night at **3:00 AM UTC**.
Backups are stored on the server for **14 days**, then purged automatically.

- Backup location: `/opt/backups/patt-db/`
- Backup format: gzipped PostgreSQL dump (`.sql.gz`)
- Naming: `patt_db_YYYY-MM-DD_HHMM.sql.gz`
- Log: `/var/log/patt-backup.log`

---

## Taking a Manual Backup

```bash
ssh hetzner
patt-backup.sh
```

Output confirms the filename and size. The file lands in `/opt/backups/patt-db/`.

---

## Listing Available Backups

```bash
ssh hetzner
patt-restore.sh --list
```

Example output:

```
Available guild database backups:

Num   Backup file                         Size
---   -----------                         ----
[1]   patt_db_2026-02-25_0300.sql.gz       28K
[2]   patt_db_2026-02-24_0300.sql.gz       27K
[3]   patt_db_2026-02-23_0300.sql.gz       27K
```

---

## Restoring the Database

> **⚠ This replaces the live database.** The script automatically takes a
> safety backup of the current state before it does anything destructive,
> so you can always undo a bad restore.

### Interactive (recommended)

```bash
ssh hetzner
patt-restore.sh
```

1. A numbered list of available backups is shown, newest first.
2. Enter the number you want to restore.
3. Type `YES` (exact) to confirm — anything else aborts.
4. The script will:
   - Take a `PRERESTORE_` safety backup of the current database
   - Stop the `patt` service
   - Drop and recreate `patt_db`
   - Restore the selected backup
   - Restart the service
   - Run a health check (up to 30s)

### Direct file (for scripting or automation)

```bash
ssh hetzner
patt-restore.sh /opt/backups/patt-db/patt_db_2026-02-24_0300.sql.gz
```

Same confirmation prompt applies — you still have to type `YES`.

---

## After a Restore

- The app restarts automatically and health-checks are run.
- If the health check fails after 30s, check logs:
  ```bash
  journalctl -u patt -n 50 --no-pager
  ```
- The safety backup taken just before the restore is kept in
  `/opt/backups/patt-db/` with a `PRERESTORE_` prefix. If you need to
  undo the restore, run `patt-restore.sh` again and select that file.

---

## Verifying Backup Integrity (optional)

To confirm a backup file is a valid, readable dump without touching the live DB:

```bash
ssh hetzner
gunzip -c /opt/backups/patt-db/patt_db_YYYY-MM-DD_HHMM.sql.gz | head -5
```

A valid dump starts with:

```
--
-- PostgreSQL database dump
--
```

---

## Scripts

| Script | Location on server |
|--------|-------------------|
| `patt-backup.sh` | `/usr/local/bin/patt-backup.sh` |
| `patt-restore.sh` | `/usr/local/bin/patt-restore.sh` |
| Cron job | `/etc/cron.d/patt-backup` |

---

## Recovering from a Bad Delete

### Background: how FKs are configured

Several tables — including `trinket_tier_ratings`, `bis_list_entries`, `gear_plan_slots` — use
`ON DELETE RESTRICT` on their foreign keys to `bis_list_sources` and `wow_items`. This means
PostgreSQL will **refuse** to delete a parent row if dependent rows exist, rather than silently
cascading the delete. You'll get an error like:

```
ERROR: update or delete on table "bis_list_sources" violates foreign key constraint
       "trinket_tier_ratings_source_id_fkey"
DETAIL: Key (id)=(3) is still referenced from table "trinket_tier_ratings".
```

This is intentional. It forces an explicit cleanup step before any destructive parent-row delete,
making accidental data wipes loud and obvious rather than quiet and irreversible.

### If you need to delete a parent row intentionally

Before deleting a `bis_list_sources` row (e.g., retiring a source):

```sql
-- Clear dependent rows first
DELETE FROM guild_identity.trinket_tier_ratings WHERE source_id = :id;
DELETE FROM guild_identity.bis_list_entries       WHERE source_id = :id;
DELETE FROM guild_identity.bis_scrape_targets     WHERE source_id = :id;
-- then delete the source
DELETE FROM guild_identity.bis_list_sources WHERE id = :id;
```

Before deleting a `wow_items` row:

```sql
-- Check what references it first
SELECT 'trinket_tier_ratings' AS tbl, COUNT(*) FROM guild_identity.trinket_tier_ratings WHERE item_id = :id
UNION ALL
SELECT 'bis_list_entries',             COUNT(*) FROM guild_identity.bis_list_entries       WHERE item_id = :id
UNION ALL
SELECT 'gear_plan_slots (desired)',    COUNT(*) FROM guild_identity.gear_plan_slots         WHERE desired_item_id = :id
UNION ALL
SELECT 'character_equipment',         COUNT(*) FROM guild_identity.character_equipment      WHERE item_id = :id;

-- Clear each, then delete
DELETE FROM guild_identity.trinket_tier_ratings WHERE item_id = :id;
-- etc.
DELETE FROM guild_identity.wow_items WHERE id = :id;
```

### If scraped data was accidentally wiped

`trinket_tier_ratings` and `bis_list_entries` are fully re-populatable from the scraper.
If they're wiped, no restore is needed — just re-run the sync:

1. Log in as GL
2. Go to **Admin → Gear Plan → BIS Sync**
3. Run **Step 4: Sync BIS Lists**

The scraper is idempotent (`ON CONFLICT DO UPDATE`). All rows will be re-inserted.
Full sync time is the same as a normal BIS sync run.

### If a restore is needed (data that can't be recomputed)

Use the standard restore procedure above. Key judgment:

| What was lost | Restore needed? |
|---------------|----------------|
| `trinket_tier_ratings` | No — re-run BIS sync |
| `bis_list_entries` | No — re-run BIS sync |
| `gear_plan_slots` (player selections) | **Yes** — player data, can't recompute |
| `character_equipment` | No — re-run equipment sync |
| `gear_plans` (player plans) | **Yes** — player data, can't recompute |
| `wow_items` | No — re-run Enrich Items |

When in doubt: take a manual backup of the current state (`patt-backup.sh`) first,
then restore from the last nightly, then re-run the relevant syncs to repopulate
computable data from the restored baseline.

### Taking a safety backup before any risky DB operation

```bash
ssh hetzner
patt-backup.sh
```

This should be your first move before any manual SQL that touches more than one row.
