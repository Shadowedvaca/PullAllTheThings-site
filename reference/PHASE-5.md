# Phase 5: Google Sheets Migration

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-4 must be complete.
> **Goal:** All guild data migrated from Google Sheets to PostgreSQL. Existing tools
> (roster form, raid admin) repointed to the new API. Sheets retained as read-only archive.

---

## What This Phase Produces

1. Migration script that pulls all data from the existing Google Apps Script API
2. Guild members, characters, Discord IDs, availability — all in PostgreSQL
3. Updated roster form that submits to the new API
4. Updated roster view page that reads from the new API
5. Raid admin dashboard updated to read from the new API
6. Data integrity verification tests
7. Documentation of what moved and what the sheet archive contains

---

## Context From Previous Phases

After Phase 4:
- Full working platform: auth, campaigns, voting, admin pages
- Guild identity tables exist but are empty (or have only test/seed data)
- The existing Google Sheet contains the real guild data:
  - Roster tab: Discord username, Discord ID, character name, realm, class, spec, role, main/alt
  - Availability tab: which days each member can raid
  - Possibly other tabs (MitoQuotes, MitoTitles, etc.)

The Google Apps Script URL is stored in the `.env` file as `GOOGLE_APPS_SCRIPT_URL`.
The existing script exposes a `doGet` endpoint that returns all roster data as JSON.

---

## Tasks

### 5.1 — Data Assessment

Before writing any code, Claude Code should:
1. Call the existing Google Apps Script URL and examine the response shape
2. Document every field available and map it to the PostgreSQL schema
3. Identify any data that doesn't have a home yet (availability, Mito quotes, etc.)
4. Flag any data quality issues (missing Discord IDs, inconsistent casing, etc.)

Create `docs/MIGRATION-MAP.md` documenting the field-by-field mapping.

### 5.2 — Migration Script (`scripts/migrate_sheets.py`)

A one-time script that:
1. Fetches all data from the Google Apps Script API
2. For each roster member:
   - Creates a guild_member record (discord_username, discord_id, display_name)
   - Assigns rank based on available data (default to Member if unknown; Mike will adjust)
   - Creates character records for each character listed (name, realm, class, spec, role, main/alt)
   - Builds armory URLs automatically
3. Handles data quality:
   - Strips whitespace from all fields
   - Normalizes main/alt to lowercase ("Main" → "main")
   - Normalizes role to the standard enum (handle "DPS" → "ranged_dps" or "melee_dps" based on spec)
   - Flags records with missing critical data (no Discord username, no character name)
4. Outputs a summary: X members imported, Y characters imported, Z issues flagged

**Run it idempotently** — if run twice, it should update existing records, not duplicate them.
Use discord_username as the unique key for members, and (name, realm) for characters.

### 5.3 — Availability Data

The availability data (which days each member can raid) doesn't have a table in the
current schema. Options:
1. Add a `member_availability` table to the common schema
2. Defer to a future raid management phase

**Recommendation:** Add the table now since we're doing the migration. Even if the raid
admin isn't fully converted yet, having the data in PostgreSQL is better than losing it.

```sql
CREATE TABLE common.member_availability (
    id SERIAL PRIMARY KEY,
    member_id INTEGER REFERENCES common.guild_members(id) ON DELETE CASCADE,
    day_of_week VARCHAR(10) NOT NULL,  -- monday, tuesday, etc.
    available BOOLEAN DEFAULT TRUE,
    notes TEXT,
    auto_signup BOOLEAN DEFAULT FALSE,
    wants_reminders BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(member_id, day_of_week)
);
```

Add the Alembic migration for this table.

### 5.4 — API Endpoints for Existing Tools

These endpoints replace the Google Apps Script doGet/doPost:

```
GET  /api/v1/guild/roster-data
    Returns data in the same shape the existing tools expect, for backwards compatibility.
    This is a transitional endpoint — eventually the tools call the proper REST endpoints.

POST /api/v1/guild/roster-submit
    Accepts roster form submissions (new members signing up).
    Creates guild_member + character records.
    Same field names the existing form sends.

GET  /api/v1/guild/availability
    Returns availability data shaped for the raid admin dashboard.

POST /api/v1/guild/availability
    Accepts availability form submissions.
```

### 5.5 — Repoint Existing Tools

Update these existing HTML files to call the new API instead of Google Apps Script:

**roster.html (roster form):**
- Change the API URL from the Google Apps Script to `/api/v1/guild/roster-submit`
- Keep the form fields identical — only the endpoint changes
- Test that submissions create records in PostgreSQL

**roster-view.html (public roster view):**
- Change the data source to `/api/v1/guild/roster-data`
- Verify the response shape matches what the page expects

**raid-admin.html:**
- Change the data source to `/api/v1/guild/availability` and `/api/v1/guild/roster-data`
- Verify all features still work (day ranking, command generation, etc.)

**Important:** These HTML files already exist in the repo root (they're the legacy
GitHub Pages content). They do NOT need to be copied from anywhere — they're already here.
The task is to:
1. Move them from the repo root into `src/patt/static/legacy/`
2. Configure FastAPI to serve them at their original URL paths (so bookmarks and Discord links don't break)
3. Update the JavaScript inside each file to call the new API instead of Google Apps Script

**File mapping:**
```
repo root (before)          →  src/patt/static/legacy/ (after)
roster.html                 →  roster.html         (served at /roster)
roster-view.html            →  roster-view.html    (served at /roster-view)
raid-admin.html             →  raid-admin.html     (served at /raid-admin)
mitos-corner.html           →  mitos-corner.html   (served at /mitos-corner)
patt-config.json            →  patt-config.json    (served at /patt-config.json)
```

The root `index.html` gets REPLACED by the new platform landing page (Phase 4's
public/index.html template), so it does not move to legacy — it just gets overwritten.

After moving, delete the original files from repo root and commit. Keep the
`google-apps-script.js` at root as a reference artifact (it's the Apps Script
backend code, not served by the website).

**Update Nginx config:** Remove the temporary legacy file location block from
`deploy/nginx/pullallthething.com.conf` (the `~ ^/(roster\.html|...)` block).
After this phase, all files are served through FastAPI and the legacy block is
no longer needed. Redeploy the Nginx config.

### 5.6 — Mito's Corner

The Mito quotes and titles data should also migrate. Add tables if not already present:

```sql
CREATE TABLE patt.mito_quotes (
    id SERIAL PRIMARY KEY,
    quote TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.mito_titles (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

Migrate existing data and update the mitos-corner.html page to use the new API.

### 5.7 — Tests

**Integration tests:**

`test_migration.py`:
- test_migration_creates_expected_member_count
- test_migration_creates_characters_for_each_member
- test_migration_handles_senjin_apostrophe
- test_migration_normalizes_role_names
- test_migration_idempotent (run twice, same record count)
- test_migration_flags_missing_discord_ids

`test_legacy_api.py`:
- test_roster_data_endpoint_matches_expected_shape
- test_roster_submit_creates_member_and_character
- test_availability_endpoint_returns_day_data
- test_availability_submit_updates_member_schedule

---

## Acceptance Criteria

- [ ] Migration script successfully imports all members and characters from Google Sheets
- [ ] Data quality issues are flagged and documented
- [ ] Availability data migrated to new table
- [ ] Roster form submits to new API and creates records in PostgreSQL
- [ ] Roster view page reads from new API and displays correctly
- [ ] Raid admin dashboard reads from new API and works correctly
- [ ] Mito quotes/titles migrated
- [ ] Legacy URLs still work (no broken bookmarks)
- [ ] Migration is idempotent
- [ ] All tests pass

---

## Post-Migration: What Mike Needs to Do

1. Run the migration script: `python scripts/migrate_sheets.py`
2. Review the output — fix any flagged data quality issues in the admin UI
3. Assign correct ranks to members (the script defaults to Member for most)
4. Map Discord roles to guild ranks in the admin UI
5. Verify the roster form, roster view, and raid admin all work
6. The Google Sheet remains as a read-only archive — don't delete it

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-5: google sheets migration"`
- [ ] Update CLAUDE.md "Current Build Status" section
