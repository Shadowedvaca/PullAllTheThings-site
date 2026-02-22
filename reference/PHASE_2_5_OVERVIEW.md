# Phase 2.5: Guild Identity & Integrity System

## Overview

This phase builds the foundational identity layer for the PATT Guild Platform. It connects
World of Warcraft character data with Discord member data to create a unified "person" model,
detect mismatches, and report integrity issues automatically.

**This phase produces:**
1. A Blizzard API integration that pulls the full guild roster + character profiles on schedule
2. A Discord bot extension that syncs server members and roles
3. A WoW addon (PATTSync) that exports guild notes and officer notes from in-game
4. A Python companion app that watches for addon exports and uploads them to the API
5. A matching engine that links characters → Discord accounts → people
6. An integrity checker that detects mismatches and orphans
7. Automated Discord reporting to #audit-channel when new issues are found

## Architecture Context

**Server:** Bluehost VPS running the PATT Guild Platform
**Domain:** pullallthething.com
**Repo:** github.com/Shadowedvaca/PullAllTheThings-site
**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL, discord.py
**Shared Package:** sv_common (handles auth, Discord bot, notifications, DB connections)

**Guild Details:**
- Guild Name: Pull All The Things
- Realm: Sen'jin (US)
- Realm Slug: `senjin`
- Guild Slug: `pull-all-the-things`
- Blizzard API Namespace: `profile-us`
- API Region: `us`

**Guild Rank Hierarchy (in-game → Discord role):**
| In-Game Rank | Discord Role | Rank Index (lower = higher) |
|---|---|---|
| Guild Leader | GM | 0 |
| Officer | Officer | 1 |
| Veteran | Veteran | 2 |
| Member | Member | 3 |
| Initiate | Initiate | 4 |

Note: Mike (Trog/Trogmoon) is the only Guild Leader → GM mapping.
All others should be 1:1 name matches.

**Known Officers and Characters:**
| Person | Discord | Main Character | Realm | Role |
|---|---|---|---|---|
| Mike | Trog | Trogmoon | Sen'jin | Guild Leader |
| Mito | Mito | Bloodvalor | Alterac Mountains | Officer |
| Rocket | Rocket | Zatañña | Sargeras | Officer |
| Shodoom | Shodoom | Shodoom | Bleeding Hollow | Officer |
| Skate | Skate | Skatefarm | Tichondrius | Officer |

## Phase Structure

This phase is broken into 4 sub-phases that should be executed in order:

| File | Sub-Phase | Description |
|---|---|---|
| PHASE_2_5A_SCHEMA_AND_BLIZZARD.md | A | PostgreSQL schema + Blizzard API client |
| PHASE_2_5B_IDENTITY_ENGINE.md | B | Matching engine + Discord sync + integrity checker + reporting |
| PHASE_2_5C_ADDON_AND_COMPANION.md | C | WoW Lua addon + Python file watcher companion app |
| PHASE_2_5D_TESTS.md | D | Complete test suite for all components |

## Dependencies on Previous Phases

- **Phase 1:** Server infrastructure (PostgreSQL, Python environment, systemd services)
- **Phase 2:** Discord bot framework (sv_common bot base, command registration, channel access)

This phase EXTENDS the existing Discord bot from Phase 2 — it does not create a new bot.

## Environment Variables Required

```bash
# Blizzard API (register at https://develop.battle.net)
BLIZZARD_CLIENT_ID=<your_client_id>
BLIZZARD_CLIENT_SECRET=<your_client_secret>

# Existing from Phase 2
DISCORD_BOT_TOKEN=<existing_bot_token>
DATABASE_URL=postgresql://user:pass@localhost/patt

# Guild Config
PATT_GUILD_REALM_SLUG=senjin
PATT_GUILD_NAME_SLUG=pull-all-the-things
PATT_AUDIT_CHANNEL_ID=<discord_channel_id_for_audit-channel>

# Companion App (on gaming PC only)
PATT_API_URL=https://pullallthething.com/api
PATT_API_KEY=<generated_api_key_for_addon_uploads>
WOW_SAVED_VARIABLES_PATH=<path_to_WoW/_retail_/WTF/Account/ACCOUNTNAME/SavedVariables>
```

## API Endpoints Created

```
# Blizzard sync (internal, triggered by scheduler)
POST /api/guild-sync/blizzard/trigger          # Manual trigger for full sync

# Addon upload (from companion app)
POST /api/guild-sync/addon-upload              # Receives SavedVariables data
GET  /api/guild-sync/addon-upload/status       # Last upload timestamp

# Identity management
GET  /api/identity/persons                     # List all known persons with links
GET  /api/identity/orphans/wow                 # WoW characters with no Discord link
GET  /api/identity/orphans/discord             # Discord members with no WoW link
GET  /api/identity/mismatches                  # Role mismatches
POST /api/identity/link                        # Manually link a character to a Discord user
POST /api/identity/confirm                     # Confirm an auto-suggested link
DELETE /api/identity/link/{link_id}            # Remove an incorrect link

# Status & reporting
GET  /api/guild-sync/status                    # Overall sync status and last run times
POST /api/guild-sync/report/trigger            # Force an integrity report to #audit-channel
```

## Data Flow Diagram

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   Blizzard API      │     │   Discord API         │     │   WoW Addon         │
│   (4x/day cron)     │     │   (real-time events   │     │   (manual trigger,  │
│                     │     │    + periodic poll)   │     │    up to 4x/day)    │
│  • Guild roster     │     │  • Member list        │     │  • Guild notes      │
│  • Character prof.  │     │  • Roles              │     │  • Officer notes    │
│  • Spec, ilvl, etc  │     │  • Join/leave events  │     │  • Last seen        │
└────────┬────────────┘     └──────────┬───────────┘     └──────────┬──────────┘
         │                             │                            │
         │                             │                    ┌───────┴───────┐
         │                             │                    │ Companion App │
         │                             │                    │ (file watcher)│
         │                             │                    └───────┬───────┘
         ▼                             ▼                            ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                                      │
│                   pullallthething.com/api                                   │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ wow_characters│  │discord_members│  │   persons    │  │ audit_issues │  │
│  │              │  │              │  │              │  │              │  │
│  │ name         │  │ discord_id   │  │ person_id    │  │ issue_type   │  │
│  │ class        │  │ username     │  │ display_name │  │ details      │  │
│  │ spec         │  │ nickname     │  │              │  │ first_seen   │  │
│  │ rank         │  │ highest_role │  │              │  │ resolved_at  │  │
│  │ guild_note   │  │              │  │              │  │ notified     │  │
│  │ officer_note │  │              │  │              │  │              │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────────────┘  │
│         │                 │                 │                              │
│         └────────►  Matching Engine  ◄──────┘                             │
│                    (links to persons)                                      │
│                          │                                                 │
│                          ▼                                                 │
│                 Integrity Checker                                          │
│                 (detects deltas)                                           │
│                          │                                                 │
│                          ▼                                                 │
│              Discord Bot → #audit-channel                                  │
│              (only NEW issues reported)                                    │
└────────────────────────────────────────────────────────────────────────────┘
```

## Migration Notes

**Existing data to import:**
- The current Google Sheet has a `DiscordIDs` tab mapping Discord names → Discord User IDs
- The `Characters` sheet has character data with Discord name, character name, class, spec, role, main/alt
- These should be imported during initial setup to seed the identity system

**Initial run expectations:**
- The Initiate Discord role was just created and has NO members assigned yet
- The first integrity report will be large — this IS the audit Mike requested
- After the initial cleanup, ongoing reports will only flag new deltas

## File Organization

```
sv_common/
  guild_sync/
    __init__.py
    blizzard_client.py        # OAuth + API calls to Battle.net
    discord_sync.py           # Discord member/role syncing
    addon_processor.py        # Parses SavedVariables data from addon
    identity_engine.py        # Matching logic, person management
    integrity_checker.py      # Comparison, mismatch detection, delta tracking
    reporter.py               # Discord embed builder for #audit-channel
    scheduler.py              # APScheduler setup for periodic tasks
    models.py                 # SQLAlchemy/Pydantic models
    schemas.py                # API request/response schemas

  guild_sync/api/
    __init__.py
    routes.py                 # FastAPI router with all endpoints

companion_app/
  patt_sync_watcher.py        # Standalone Python script for gaming PC
  requirements.txt
  README.md

wow_addon/
  PATTSync/
    PATTSync.toc
    PATTSync.lua
    README.md
```
