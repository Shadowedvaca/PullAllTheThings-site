# Phase 5: Google Sheets → PostgreSQL Migration Map

> **HISTORICAL REFERENCE ONLY.** This migration was completed in Phase 5 (2025).
> The Google Sheets data is fully in PostgreSQL. This document is kept for audit
> purposes only — the schema it references (`common.guild_members`, `common.characters`)
> has since been superseded by the Phase 2.7 3NF rebuild (`guild_identity.players`,
> `guild_identity.wow_characters`).
>
> Original field-by-field mapping from the Google Apps Script API response
> to the PostgreSQL schema. Created during Phase 5 data assessment.

---

## Google Apps Script Response Shape

The existing Apps Script exposes a `doGet` endpoint that returns:

```json
{
  "success": true,
  "availability": [
    {
      "discord": "trog",
      "monday": true,
      "tuesday": true,
      "wednesday": false,
      "thursday": true,
      "friday": true,
      "saturday": true,
      "sunday": false,
      "notes": "Sundays are my family night",
      "updated": "2025-01-15T12:00:00Z",
      "autoSignup": true,
      "wantsReminders": false
    }
  ],
  "characters": [
    {
      "discord": "trog",
      "character": "Trogmoon",
      "class": "Druid",
      "spec": "Balance",
      "role": "Ranged",
      "mainAlt": "Main"
    }
  ],
  "discordIds": {
    "trog": "195547238959677441"
  },
  "validationIssues": [],
  "mitoQuotes": [
    "Less QQ more pew pew"
  ],
  "mitoTitles": [
    "Bubble Hearth Champion"
  ]
}
```

---

## Field Mapping

### Availability → `common.member_availability`

| Apps Script Field | Type | PostgreSQL Column | Notes |
|---|---|---|---|
| `discord` | string | (join key → `guild_members.discord_username`) | Used to find member |
| `monday` | boolean | `day_of_week='monday'` + `available` | One row per day |
| `tuesday` | boolean | `day_of_week='tuesday'` + `available` | |
| `wednesday` | boolean | `day_of_week='wednesday'` + `available` | |
| `thursday` | boolean | `day_of_week='thursday'` + `available` | |
| `friday` | boolean | `day_of_week='friday'` + `available` | |
| `saturday` | boolean | `day_of_week='saturday'` + `available` | |
| `sunday` | boolean | `day_of_week='sunday'` + `available` | |
| `notes` | string | `notes` | Stored on first day row or a dedicated "all" row |
| `autoSignup` | boolean | `auto_signup` | Stored on each day's row |
| `wantsReminders` | boolean | `wants_reminders` | Stored on each day's row |

### Characters → `common.characters`

| Apps Script Field | Type | PostgreSQL Column | Normalization |
|---|---|---|---|
| `discord` | string | (join key → `guild_members.discord_username`) | Strip whitespace |
| `character` | string | `characters.name` | Strip whitespace |
| `class` | string | `characters.class_` | No change needed |
| `spec` | string | `characters.spec` | No change needed |
| `role` | string | `characters.role` | `"Tank"→"tank"`, `"Healer"→"healer"`, `"Melee"→"melee_dps"`, `"Ranged"→"ranged_dps"` |
| `mainAlt` | string | `characters.main_alt` | `"Main"→"main"`, `"Alt"→"alt"` |
| *(derived)* | | `characters.realm` | Default `"Sen'jin"` for all PATT characters |
| *(derived)* | | `characters.armory_url` | Built as `https://worldofwarcraft.blizzard.com/en-us/character/us/senjin/{name}` |

### Discord IDs → `common.guild_members`

| Apps Script Field | Type | PostgreSQL Column | Notes |
|---|---|---|---|
| `discordIds[username]` | string (snowflake) | `guild_members.discord_id` | Applied to the member with matching `discord_username` |

### Mito Content → `patt.mito_quotes` / `patt.mito_titles`

| Apps Script Field | Type | PostgreSQL Table | Notes |
|---|---|---|---|
| `mitoQuotes[n]` | string | `patt.mito_quotes.quote` | One row per quote |
| `mitoTitles[n]` | string | `patt.mito_titles.title` | One row per title |

---

## Member Records → `common.guild_members`

Members are inferred from the `availability` array (one entry per player).
Characters provide additional info about the same player.

| Derived From | PostgreSQL Column | Value |
|---|---|---|
| `availability[].discord` | `discord_username` | Raw Discord username from sheet |
| `discordIds[username]` | `discord_id` | Numeric Discord snowflake if present |
| *(default)* | `rank_id` | Member rank (level 2) — Mike adjusts in admin UI |
| *(default)* | `rank_source` | `"manual"` |

---

## Data Quality Notes

- **Realm assumption:** All PATT characters are on Sen'jin — `"Sen'jin"` (with apostrophe)
- **Role normalization:** The old form sends `"Tank"`, `"Healer"`, `"Melee"`, `"Ranged"` (capitalized).
  The DB schema uses `"tank"`, `"healer"`, `"melee_dps"`, `"ranged_dps"`.
- **Main/Alt normalization:** `"Main"` → `"main"`, `"Alt"` → `"alt"`
- **Apostrophe in realm:** `"Sen'jin"` must be handled correctly in UNIQUE(name, realm) queries
- **Discord username uniqueness:** The script uses `discord_username` as the primary key for members.
  If two availability rows have the same discord name, they'll be merged (last wins).
- **Missing Discord IDs:** Members not in `discordIds` will have `discord_id = NULL`.
  The migration flags these for manual resolution.

---

## What the Migration Script Does

1. Fetches `GOOGLE_APPS_SCRIPT_URL` (GET)
2. For each entry in `availability`:
   - Upsert `guild_members` (keyed on `discord_username`)
   - Set `discord_id` from `discordIds` map if available
   - Upsert `member_availability` rows (one per day, UNIQUE on `member_id + day_of_week`)
3. For each entry in `characters`:
   - Find member by `discord_username`
   - Upsert `characters` (keyed on `name + realm`)
   - Build armory URL automatically
4. Migrate `mitoQuotes` → `patt.mito_quotes` (INSERT if not already present)
5. Migrate `mitoTitles` → `patt.mito_titles` (INSERT if not already present)
6. Output summary: members imported, characters imported, issues flagged

---

## Archive Notes

The Google Sheet remains intact as a read-only archive after migration.
Do not delete or modify the sheet — it is the historical record.
The Apps Script can be left deployed but is no longer used by the platform.
