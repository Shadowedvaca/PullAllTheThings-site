# sv-common Audit Report

> **Status:** Draft — awaiting Mike's review and comments
> **Date:** 2026-03-18
> **Scope:** Cross-repo inventory, alignment analysis, and spinoff recommendation

---

## 1. Executive Summary

`sv_common` was written as a shared backend library, but it currently lives as a **copy-pasted subdirectory** inside each app repo with no formal link between them. The result is three slowly diverging codebases. A bug fix in PATT's `sv_common` does not reach satt or shadowedvaca. A new feature (errors, feedback, config_cache) added in PATT doesn't exist in the others.

**The gap is already significant.** satt's copy is stuck at roughly the pre-Phase-4 state of PATT, still imports a package name (`patt.config`) that was renamed months ago and would crash on import if satt spun up a fresh server. shadowedvaca reimplemented auth, invite codes, and DB engine in its own app package, diverging on JWT claims by design.

The right fix is two things at once: make `sv-common` a standalone installable package containing only generic, domain-agnostic infrastructure (auth, encryption, DB engine, error catalogue, feedback), and move all WoW guild-specific code out of sv_common and into PATT's own application package where it belongs.

---

## 2. What Each Repo Has

### 2.1 Full Inventory Table

| Module | PATT | shadowedvaca | satt |
|--------|:----:|:------------:|:----:|
| `auth/passwords.py` | ✅ | ✅ | ✅ |
| `auth/jwt.py` | ✅ | ❌ (reimpl in `sv_site/auth.py`) | ✅ (drifted) |
| `auth/invite_codes.py` | ✅ | ❌ (reimpl in `sv_site/auth.py`) | ✅ |
| `db/engine.py` | ✅ | ❌ (reimpl in `sv_site/database.py`) | ✅ |
| `db/models.py` | ✅ (full 3-schema ORM) | ❌ (own `sv_site/models.py`) | ✅ (old copy of PATT) |
| `db/seed.py` | ✅ | ❌ | ✅ |
| `crypto.py` | ✅ | ❌ | ❌ |
| `config_cache.py` | ✅ | ❌ | ❌ |
| `errors/` | ✅ | ❌ | ❌ |
| `feedback/` | ✅ | ❌ (implemented in `sv_site/`) | ❌ |
| `notify/` | ✅ (placeholder) | ❌ | ❌ |
| `identity/` | ✅ | ❌ | ✅ (old copy) | → guild_portal |
| `discord/bot.py` + guild Discord | ✅ | ❌ | ✅ (old copy) | → guild_portal |
| `discord/messaging utils` | ✅ | ❌ | ✅ | → sv-common |
| `guild_sync/` | ✅ (full, 30+ files) | ❌ | ✅ (older subset, ~20 files) | → guild_portal |

**shadowedvaca-site** uses sv_common as a minimal package — only `auth/passwords.py` is actual shared code. Everything else (JWT, invite codes, DB engine, ORM models) is reimplemented in `sv_site` with different design choices. This was actually the right instinct, just not formalized.

**saltallthethings-site** has a near-complete copy of PATT's sv_common frozen at an older point — missing all Phase 4+ additions. After the migration, satt will use the sv-common package for infrastructure and won't need guild_sync at all — it was carrying that code for no reason.

---

## 3. Drift Analysis

### 3.1 PATT vs satt

These two repos share the same conceptual codebase and have the most modules in common. The drift is **substantial**.

#### `auth/jwt.py` — Broken import
satt still has the old package name from before the `patt` → `guild_portal` rename:

```python
# satt (BROKEN — patt package no longer exists)
from patt.config import get_settings

# PATT (current)
from guild_portal.config import get_settings
```

satt's sv_common would fail on import if it ever tried to use JWT. This is a silent land mine.

#### `discord/bot.py` — Major divergence
PATT: 245 lines. satt: 131 lines.

PATT's bot has accumulated significant additions satt never received:
- `voice_states` intent (attendance tracking)
- `config_cache` usage for guild name / accent color
- Guild quote slash command registration
- `VoiceAttendanceCog` conditional loading
- Full `on_member_join` onboarding flow

#### `guild_sync/blizzard_client.py` — 917 diff lines
satt's copy is a much earlier version. PATT's has added: AH auction/commodity fetching, M+ keystone profile, achievements, connected realm ID lookup, and multi-batch character sync with adaptive cadence. The two files share a common ancestor but have substantially diverged.

#### `guild_sync/scheduler.py` — 958 diff lines
satt's scheduler runs 4 jobs. PATT's runs 13+: crafting_sync, wcl_sync, bnet_character_refresh, attendance_processing, ah_sync, weekly_error_digest, addon_data_sync, drift_scan, progression_sync all don't exist in satt.

#### Modules only in PATT (added after the satt snapshot was taken)
- `crypto.py`
- `config_cache.py`
- `errors/` (Phase 6.1)
- `feedback/` (Phase F.2)
- `guild_sync/ah_service.py`, `ah_sync.py`
- `guild_sync/wcl_sync.py`, `warcraftlogs_client.py`
- `guild_sync/raiderio_client.py`
- `guild_sync/progression_sync.py`
- `guild_sync/bnet_character_sync.py`
- `guild_sync/attendance_processor.py`
- `discord/voice_attendance.py`

### 3.2 PATT vs shadowedvaca

These repos intentionally share less — shadowedvaca is a different type of application (Hub platform, not a guild portal). But the divergence in what they do share is worth noting.

#### `auth/passwords.py` — Functionally identical, different line endings
PATT uses LF; shadowedvaca uses CRLF (Windows checkout). The code is identical. The only "drift" here is cosmetic.

#### JWT — Different claims by design
The two apps use JWT for different purposes, so the claim sets intentionally differ:

```python
# PATT: guild platform — tracks member identity and rank gate
create_access_token(user_id, member_id, rank_level, expires_minutes)
# payload: {user_id, member_id, rank_level, exp, iat}

# shadowedvaca: hub platform — tracks username and admin flag
create_access_token(user_id, username, is_admin)
# payload: {user_id, username, is_admin, exp, iat}
```

This isn't a bug — it's different domain requirements. A shared JWT module needs to handle this via parameterization.

#### Invite codes — Different fields by design
```python
# PATT: guild-centric, linked to a player, 72h default
generate_invite_code(db, player_id, created_by_id, expires_hours=72)

# shadowedvaca: hub-centric, optional tool permissions, 48h default
generate_invite_code(db, created_by_user_id, expires_hours=48, permissions=None)
```

Again intentional divergence — both represent the same concept (invite codes) but with app-specific fields. This is solvable with a more generic shared base.

---

## 4. Proposed Spinoff Architecture

### 4.1 Core Principle

Two things need to happen simultaneously:

1. **`sv-common` becomes a standalone installable package** containing only genuinely reusable, domain-agnostic patterns — auth, encryption, DB engine, error catalogue, feedback collection, and generic Discord messaging utilities. A guild is a WoW concept. Blizzard API sync, identity matching, crafting, raid attendance, and character data have no business living in a shared library.

2. **Guild code moves into `guild_portal`** — PATT's own application package. Everything currently in `sv_common/guild_sync/`, `sv_common/identity/`, and the guild-specific Discord modules moves to `src/guild_portal/guild/` (or similar). PATT owns it; other apps never see it.

The result: sv-common is a clean infrastructure toolkit. PATT is a guild platform that happens to use that toolkit.

### 4.2 What Belongs Where

#### Stays in sv-common — generic infrastructure

```
sv-common/sv_common/
├── auth/
│   ├── passwords.py        ← bcrypt (universally reusable)
│   ├── jwt.py              ← JWT create/decode (parameterized claims)
│   └── invite_codes.py     ← invite code generate/validate/consume (parameterized)
├── db/
│   ├── engine.py           ← async SQLAlchemy engine factory
│   └── base.py             ← DeclarativeBase only
├── discord/
│   ├── messaging.py        ← post embed/text to channel, send DM (generic)
│   └── bot_factory.py      ← create a configured Bot instance (generic)
├── ai/
│   └── conversation.py     ← generic AI conversation patterns (future)
├── crypto.py               ← Fernet encryption utilities
├── errors/                 ← portable error catalogue (no Discord, no app deps)
├── feedback/               ← feedback collection + Hub sync client
└── notify/                 ← generic notification dispatch (future)
```

**The test:** if you could drop this module into a non-WoW app (a book club site, a podcast platform, a SaaS tool) and it would still make sense — it belongs in sv-common. If it requires knowing what a guild rank, realm slug, or Blizzard OAuth token is — it does not.

#### Moves to `guild_portal` — PATT owns this

```
guild_portal/guild/             ← new home for everything currently in sv_common/guild_sync/
├── blizzard_client.py          ← Blizzard API OAuth2 + endpoints
├── scheduler.py                ← APScheduler guild sync jobs
├── db_sync.py                  ← roster → guild_identity.wow_characters
├── discord_sync.py             ← Discord members → guild_identity.discord_users
├── identity_engine.py          ← character-to-player matching
├── crafting_service.py         ← Crafting Corner data layer
├── crafting_sync.py            ← profession sync pipeline
├── progression_sync.py         ← raid/M+/achievement sync
├── raiderio_client.py          ← Raider.IO API client
├── warcraftlogs_client.py      ← WCL GraphQL client
├── wcl_sync.py                 ← WCL parse sync pipeline
├── bnet_character_sync.py      ← Battle.net OAuth character auto-claim
├── ah_service.py / ah_sync.py  ← AH price data
├── attendance_processor.py     ← raid attendance reconciliation
├── drift_scanner.py            ← data drift detection
├── integrity_checker.py        ← data quality checker
├── mitigations.py              ← auto-resolution for audit issues
├── reporter.py                 ← Discord embed reporting (guild audit channel)
├── sync_logger.py              ← sync_log context manager
├── rules.py / matching_rules/  ← identity matching rule engine
├── migration.py                ← one-time CSV import utility
├── onboarding/                 ← Discord DM onboarding flow
└── api/                        ← FastAPI routes for sync triggers + crafting

guild_portal/identity/          ← new home for sv_common/identity/
├── ranks.py                    ← GuildRank CRUD
├── members.py                  ← Player CRUD
└── characters.py               ← WoW character management

guild_portal/discord/           ← guild-specific Discord extensions
├── bot.py                      ← guild bot instance + event handlers
├── channel_sync.py             ← Discord channel → discord_channels table
├── voice_attendance.py         ← VoiceAttendanceCog for raid tracking
└── role_sync.py                ← Discord role → GuildRank sync
```

`config_cache.py` also moves — its current getters (`get_guild_name()`, `get_home_realm_slug()`, `get_realm_display_name()`, etc.) are PATT-specific. sv-common can offer a generic `config_cache` pattern (set/get a dict, a few generic helpers) but the guild-specific field accessors live in `guild_portal`.

#### Models stay in each app

`sv_common/db/models.py` is a monolith covering three schemas. It does not move to sv-common as-is. sv-common provides `DeclarativeBase` only. Each app defines its own ORM models.

### 4.3 Packaging Strategy

**Recommended: pip install from Git tag**

```toml
# pyproject.toml — any consuming app
[project]
dependencies = [
    "sv-common @ git+https://github.com/Shadowedvaca/sv-common@v1.0.0",
]

# If the app uses Discord messaging utilities:
# "sv-common[discord] @ git+https://github.com/Shadowedvaca/sv-common@v1.0.0",
```

No private PyPI needed. Git tags provide versioning. When ready for a proper registry (GitHub Packages, Gemfury), only the URL changes — the API is identical.

**Optional extras** are only needed if the app uses Discord utilities (requires `discord.py`) or AI conversations (requires `anthropic`). Core — auth, db, crypto, errors, feedback — has no optional deps.

**Avoid Git submodules** — no version pinning story, painful to maintain.

### 4.4 Breaking Points to Fix Before Extraction

#### In sv-common (before extracting the package)

| Issue | File | Fix |
|-------|------|-----|
| `from patt.config import get_settings` | satt `auth/jwt.py` | Remove app import; read from env directly or accept as param |
| `from guild_portal.config import get_settings` | PATT `auth/jwt.py` | Same fix |
| JWT claims are app-specific | `auth/jwt.py` | Accept `extra_claims: dict`; app passes `{"member_id": x, "rank_level": y}` |
| Invite code `player_id` / `created_by_user_id` naming | `auth/invite_codes.py` | Rename to generic `owner_id`; add optional `metadata: dict` for app-specific fields |
| Invite code expiry defaults differ (72h vs 48h) | `auth/invite_codes.py` | Remove default; callers pass explicitly |
| `feedback/_hub_client.py` Hub URL is hardcoded-ish | `feedback/` | Make hub URL a config value passed at call site or env var |
| `discord/bot.py` registers guild slash commands | `discord/bot.py` | Bot factory in sv-common creates a blank instance; guild commands registered by PATT's own bot module |

#### In PATT (moving guild code out of sv_common into guild_portal)

| Task | Detail |
|------|--------|
| Create `guild_portal/guild/` | Move all of `sv_common/guild_sync/` here; update all imports |
| Create `guild_portal/identity/` | Move `sv_common/identity/` here; update imports |
| Create `guild_portal/discord/` | Move bot.py, channel_sync.py, voice_attendance.py, role_sync.py here |
| Move config_cache guild accessors | Generic cache stays in sv-common; `get_guild_name()`, `get_home_realm_slug()` etc. move to guild_portal |
| Move `db/models.py` | Stays in PATT — it's already PATT-specific; just moves from sv_common/db/ to guild_portal/db/ |
| Move `db/seed.py` | Move to guild_portal — seeding guild ranks is PATT-specific |
| Update all cross-module imports | `from sv_common.guild_sync.X import Y` → `from guild_portal.guild.X import Y` |

### 4.5 What Each App Ends Up With

**PATT (guild_portal)**
```
sv-common[discord]          ← auth, db engine, crypto, errors, feedback, Discord messaging
guild_portal/guild/         ← all WoW/guild sync (previously sv_common/guild_sync/)
guild_portal/identity/      ← guild identity (previously sv_common/identity/)
guild_portal/discord/       ← guild bot + guild-specific Discord (previously sv_common/discord/)
guild_portal/db/models.py   ← full ORM (stays in app, as it always logically should have)
```

**satt (saltallthethings)**
```
sv-common[discord]          ← auth, db engine, crypto, errors, feedback, Discord messaging
satt/                       ← satt-specific features; no guild code
```
satt doesn't need Blizzard sync, character data, or guild identity. Once guild code moves to PATT, satt stops silently carrying dead weight it was never using.

**shadowedvaca (sv_site)**
```
sv-common                   ← auth, db engine, crypto, errors, feedback
sv_site/                    ← Hub platform; own auth.py extends sv-common JWT
```
Minimal change from today. shadowedvaca already made the right call by keeping sv_common shallow.

---

## 5. Migration Path

Two workstreams run in parallel but are independently shippable: extracting sv-common to its own repo, and relocating guild code within PATT. Do them together or separately — either order works.

### Phase SV-1: Extract sv-common (new repo)
1. Create `github.com/Shadowedvaca/sv-common`
2. Seed it with the generic modules from PATT's current sv_common: `auth/`, `db/engine.py`, `db/base.py`, `crypto.py`, `errors/`, `feedback/`, `notify/`, `discord/messaging.py` (generic channel/DM only)
3. Fix all app-import violations (remove `from guild_portal.config import get_settings` from jwt.py; read from env instead)
4. Parameterize JWT claims (`extra_claims: dict`) and invite code fields (generic `owner_id`, remove expiry default)
5. Write a `pyproject.toml` with optional extras `[discord]` for the Discord messaging utilities
6. Tag `v1.0.0`
7. Add tests for all sv-common modules (many already exist in PATT's test suite — port them)

### Phase SV-2: Move guild code inside PATT
1. Create `src/guild_portal/guild/` and move all of `sv_common/guild_sync/` there
2. Create `src/guild_portal/identity/` and move `sv_common/identity/` there
3. Move guild-specific Discord modules (`bot.py`, `channel_sync.py`, `voice_attendance.py`, `role_sync.py`) to `src/guild_portal/discord/`
4. Move `sv_common/db/models.py` and `sv_common/db/seed.py` to `src/guild_portal/db/`
5. Move guild-specific `config_cache` accessors into a `guild_portal/config.py` alongside the existing `get_settings()`
6. Update all internal imports throughout `guild_portal/` — `from sv_common.guild_sync.X` → `from guild_portal.guild.X` etc.
7. Update all `sv_common/guild_sync/api/` routes to sit under `guild_portal/api/` (they likely already have API route registration there)
8. Run the full test suite; fix any import errors

### Phase SV-3: Wire PATT to the new sv-common package
1. Add `sv-common[discord] @ git+https://github.com/Shadowedvaca/sv-common@v1.0.0` to PATT's `requirements.txt` / `pyproject.toml`
2. Delete `src/sv_common/` from PATT entirely
3. Update remaining imports in guild_portal that still reference sv-common's generic modules (auth, crypto, errors, feedback) — these now come from the installed package
4. Run the full test suite to confirm clean

### Phase SV-4: Migrate satt
1. Add `sv-common[discord] @ git+https://github.com/Shadowedvaca/sv-common@v1.0.0` to satt's dependencies
2. Delete satt's local `sv_common/` copy
3. satt gets current auth, errors, and feedback; it never had a use for guild_sync so nothing is lost
4. Fix satt's app code for any signature changes (JWT extra_claims, invite code owner_id, expiry required)
5. Decide whether satt wants its own `guild/` equivalent or is a simpler app — that's satt's problem now, not sv-common's

### Phase SV-5: Migrate shadowedvaca (optional)
1. shadowedvaca currently only uses `auth/passwords.py` — already the right instinct
2. Optionally update `sv_site/auth.py` to use sv-common's parameterized JWT: pass `extra_claims={"username": x, "is_admin": y}`
3. This is low urgency — shadowedvaca's implementation is clean enough as-is

### Ongoing governance
- All sv-common changes go through a PR in the sv-common repo; tag for every release
- Breaking changes bump minor version; patches are safe to auto-upgrade
- Each app pins to a specific tag (`@v1.2.0`), never `@main`
- To ship a fix to all consumers: one PR to sv-common → tag → each app opens a version bump PR

---

## 6. Quick Reference: Current Divergence Risk by Module

Annotated with the proposed destination after migration.

| Module | Risk Level | Destination | Notes |
|--------|-----------|-------------|-------|
| `auth/passwords.py` | 🟢 Low | sv-common | All three identical (cosmetic CRLF difference in shadowedvaca) |
| `auth/jwt.py` | 🔴 High | sv-common (parameterized) | satt has broken import; shadowedvaca has diverged claims by design |
| `auth/invite_codes.py` | 🟡 Medium | sv-common (parameterized) | PATT and satt identical; shadowedvaca diverged by design |
| `db/engine.py` | 🟢 Low | sv-common | PATT and satt identical; shadowedvaca reimplemented separately |
| `db/models.py` | 🔴 High | Each app's own `db/models.py` | satt has old copy; will never be current — not sv-common's job |
| `db/seed.py` | 🟡 Medium | guild_portal/db/ | Seeding guild ranks is PATT-specific |
| `crypto.py` | 🟢 Low | sv-common | Generic Fernet utilities; only in PATT today but belongs in sv-common |
| `config_cache.py` | 🟡 Medium | Split | Generic cache pattern → sv-common; guild field getters → guild_portal |
| `errors/` | 🟢 Low | sv-common | Already designed as portable; no app deps |
| `feedback/` | 🟢 Low | sv-common | Already designed as portable client-side library |
| `notify/` | 🟢 Low | sv-common | Placeholder; stays generic |
| `discord/channels.py` + `dm.py` | 🟢 Low | sv-common/discord/ | Generic messaging utilities |
| `discord/bot.py` | 🔴 High | guild_portal/discord/ | 245 vs 131 lines; guild-specific event handlers and slash commands |
| `discord/role_sync.py` | 🟡 Medium | guild_portal/discord/ | Syncs Discord roles to GuildRank — guild concept |
| `discord/channel_sync.py` | 🟡 Medium | guild_portal/discord/ | Syncs to discord_channels table in guild_identity schema |
| `discord/voice_attendance.py` | 🟡 Medium | guild_portal/discord/ | Raid attendance — pure guild concept |
| `identity/` | 🟡 Medium | guild_portal/identity/ | WoW guild identity (players, characters, ranks) |
| `guild_sync/` (all 30+ files) | 🔴 High | guild_portal/guild/ | WoW-specific; not sv-common's concern |

---

## 7. Open Questions for Mike

1. **Should shadowedvaca's JWT be unified with sv-common's?** shadowedvaca's JWT uses `username + is_admin` claims vs PATT's `member_id + rank_level`. A parameterized sv-common JWT can serve both via `extra_claims`. Low urgency since shadowedvaca's implementation is clean — but worth deciding so shadowedvaca doesn't maintain a separate JWT implementation forever.

2. **Private repo or public?** sv-common will contain infrastructure patterns but no secrets. Making it public (or at least org-internal) has no obvious downside and makes pip installation trivial. If there's IP concern, a private repo with a GitHub deploy key in each app's CI also works.

3. **Generic config_cache or drop it from sv-common?** The current `config_cache.py` is guild-specific. sv-common could offer a generic pattern (seed an in-process dict from a single-row DB table, get/set helpers) that any app could use, with apps defining their own typed accessors on top. Or each app just rolls its own. Worth deciding before SV-1.

4. **What does satt actually need?** Now that guild code is moving to PATT, satt's sv_common becomes mostly dead weight. satt should install sv-common core (auth, db engine, errors, feedback) and build its own app-specific modules. But this raises the question: does satt have a Discord bot? Its own identity concepts? Those decisions don't block the sv-common extraction but affect what satt's Phase SV-4 looks like.

---

*Once you've reviewed and commented, I'll turn this into a proper ARCHITECTURE.md in the sv-common repo and document the migration checklist.*
