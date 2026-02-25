# PATT Database — Backup & Restore

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
Available PATT database backups:

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
