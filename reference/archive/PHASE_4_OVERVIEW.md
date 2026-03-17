# Phase 4: Guild Portal — Multi-Guild Platform Release

## Vision

Transform the PATT guild platform from a single-guild, single-tenant application into
**Guild Portal** — a deployable, configurable platform any WoW guild can run. Ships with
Docker packaging, a first-run setup wizard, Battle.net OAuth identity verification, and
integrations with Raider.IO, Warcraft Logs, and Blizzard Auction House data on top of
the existing Blizzard API and Discord bot.

**Product Name:** Guild Portal (working title — may change)

---

## Sub-Phases

| Phase | Name | Migration | Status | Depends On |
|-------|------|-----------|--------|------------|
| 4.0 | Config Extraction & Genericization | 0031 | ✅ Complete | — |
| 4.1 | Setup Wizard | 0033 | ✅ Complete | 4.0 |
| 4.2 | Docker & Environments | — | ✅ Complete | 4.0 |
| 4.3 | Blizzard API Expansion | 0034 | ✅ Complete | 4.0 |
| 4.4 | Raider.IO Integration | 0036 | ✅ Complete | 4.3 |
| 4.4.1 | Battle.net OAuth Account Linking | 0037 | ✅ Complete | 4.1, 4.4 |
| 4.4.2 | Character Auto-Claim on OAuth | — | Planned | 4.4.1 |
| 4.4.3 | Onboarding Activation & OAuth Integration | — | Planned | 4.4.2 |
| 4.4.4 | Data Quality Simplification | — | Planned | 4.4.3 |
| 4.5 | Warcraft Logs Integration | TBD | Deferred | 4.0 |
| 4.6 | Auction House Pricing | TBD | Deferred | 4.3 |

> **Note on 4.5 and 4.6:** These phases are deferred until 4.4.1–4.4.4 are complete.
> Their migration numbers (0034/0035 as originally planned) are superseded — assign
> actual numbers at implementation time. They remain in the roadmap and their design
> docs are unchanged.

### Dependency Graph

```
4.0 Config Extraction (✅)
 ├── 4.1 Setup Wizard (✅)
 ├── 4.2 Docker & Environments (✅)
 ├── 4.3 Blizzard API Expansion (✅)
 │    └── 4.4 Raider.IO (✅)
 │         └── 4.4.1 Battle.net OAuth Linking
 │              └── 4.4.2 Character Auto-Claim
 │                   └── 4.4.3 Onboarding Activation
 │                        └── 4.4.4 Data Quality Simplification
 ├── 4.5 Warcraft Logs (deferred — independent of 4.4.x)
 └── [4.3] 4.6 AH Pricing (deferred — independent of 4.4.x)
```

Phases 4.5 and 4.6 are independent of the 4.4.x track and can begin as soon as
their respective prerequisites (4.0 and 4.3) are met — but they are lower priority
than the OAuth identity work.

---

## Architecture Changes

### New Table: `common.site_config`

Single-row guild configuration table replacing all hardcoded guild identity values.
Drives the Jinja2 context processor so templates render guild-specific branding automatically.
Also stores feature flags (guild quotes, contests) and the `setup_complete` flag that gates
the first-run wizard.

### New Table: `common.rank_wow_mapping`

Maps WoW in-game rank indices (0–9) to platform rank IDs. Replaces the hardcoded
`RANK_NAME_MAP` dict in `blizzard_client.py`. Configured via the setup wizard and editable
in admin.

### Genericized Features

- **Mito Quotes → Guild Quotes** — `patt.mito_quotes`/`mito_titles` renamed to
  `patt.guild_quotes`/`patt.guild_quote_titles`. Slash command renamed `/quote`.
  Feature gated behind `site_config.enable_guild_quotes`.
- **Contest Agent** — References to "PATT" removed from message templates. Accent color
  read from `site_config.accent_color_hex` instead of hardcoded `0xD4A84B`.

### Docker Packaging

- `Dockerfile` — Python 3.11 + uvicorn
- `docker-compose.yml` — app + PostgreSQL 16 + Caddy (auto-SSL)
- Caddy supports both subdomain routing (`guild.yourdomain.com`) and custom domains
- PATT gets three environments: `dev.pullallthethings.com`, `test.pullallthethings.com`,
  `pullallthethings.com`

### New Integrations

| Integration | Auth | New DB Tables | Scheduler | Status |
|-------------|------|---------------|-----------|--------|
| Blizzard Raids/M+/Achievements | Existing Blizzard creds | `character_raid_progress`, `character_mythic_plus`, `character_achievements`, `progression_snapshots` | Added to Blizzard sync pipeline | ✅ Complete |
| Raider.IO | None (free, public) | `raiderio_profiles` | After Blizzard sync | ✅ Complete |
| Battle.net OAuth | User Authorization Code flow | `battlenet_accounts` | Daily character refresh (3 AM UTC) | Phase 4.4.1–4.4.2 |
| Warcraft Logs | OAuth2 client credentials (per guild) | `wcl_config`, `character_parses`, `raid_reports` | Daily, independent pipeline | Deferred (4.5) |
| Blizzard AH | Existing Blizzard creds | `tracked_items`, `item_price_history` | Hourly for tracked items | Deferred (4.6) |

### Last-Login Optimization

All character-level API calls (Blizzard profiles, professions, raids, M+, achievements,
Raider.IO) skip characters whose `last_login_timestamp` hasn't changed since the last sync.
Expected 50–70% reduction in API calls. Weekly full sweep ignores the optimization to catch
edge cases.

---

## Environment Strategy (PATT)

| Environment | Domain | Database | Purpose |
|-------------|--------|----------|---------|
| Dev | `dev.pullallthethings.com` | `patt_db_dev` | Active development, may be unstable |
| Test | `test.pullallthethings.com` | `patt_db_test` | Pre-production validation, stable |
| Prod | `pullallthethings.com` | `patt_db` | Live, guild members use this |

All three run as separate Docker containers on the Hetzner server, each with their own
`.env` file, database, and Discord bot token (dev/test use a test Discord server).

---

## What This Phase Does NOT Do

- Multi-tenancy (shared database for multiple guilds) — each guild is its own instance
- Payment/billing — Guild Portal is free/self-hosted for now
- Guild Portal marketing site or landing page
- Mobile app or native client
- Warcraft Logs combat log uploading (guilds still upload via WCL's own client)
- In-game addon changes (GuildSync addon works as-is)
- Forcing Battle.net OAuth — members who decline can still use the site with manual character linking

---

## Success Criteria

1. A new guild leader can go from `docker compose up` to a fully working guild portal
   in under 30 minutes using only the setup wizard and guided instructions
2. PATT production instance is unaffected throughout — no downtime, no data loss
3. Dev and test environments are live on subdomains with isolated databases
4. All existing tests pass (475+ unit tests through Phase 4.4)
5. Raider.IO M+ scores visible on roster page ✅
6. A member can click "Connect Battle.net," approve on Blizzard's page, and see all
   their characters automatically linked within seconds
7. New Discord members are guided through registration + OAuth via bot DM with no
   officer intervention
8. Data quality page shows OAuth coverage rate and operational issues — no fuzzy
   matching clutter
9. Last-login optimization measurably reduces Blizzard API call volume ✅
10. Warcraft Logs parse data queryable for characters with uploaded logs (deferred 4.5)
11. AH prices tracked and displayed for guild-configured items (deferred 4.6)

---

## File Index

### Completed
- `reference/archive/PHASE_4_0_CONFIG_EXTRACTION.md` — Hardcoded value extraction, genericization
- `reference/archive/PHASE_4_1_SETUP_WIZARD.md` — First-run web-based setup wizard
- `reference/archive/PHASE_4_2_DOCKER_ENVIRONMENTS.md` — Docker packaging, Caddy, dev/test/prod
- `reference/PHASE_4_3_BLIZZARD_EXPANSION.md` — Last-login optimization, raids, M+, achievements
- `reference/PHASE_4_4_RAIDERIO_INTEGRATION.md` — Raider.IO M+ and raid progression

### Active (4.4.x Battle.net OAuth Track)
- ~~`reference/PHASE_4_4_1_BNET_OAUTH_ACCOUNT_LINKING.md`~~ — ✅ Complete — archived
- `reference/PHASE_4_4_2_CHARACTER_AUTO_CLAIM.md` — `/profile/user/wow` fetch, auto-link, Player Manager locks
- `reference/PHASE_4_4_3_ONBOARDING_ACTIVATION.md` — Wire `on_member_join`, conversation flow update, OAuth as finish line
- `reference/PHASE_4_4_4_DATA_QUALITY_SIMPLIFICATION.md` — Rule audit, fuzzy matching removal, manual add UI, DQ page rewrite

### Deferred
- `reference/PHASE_4_5_WARCRAFT_LOGS.md` — Warcraft Logs parses, reports, attendance
- `reference/PHASE_4_6_AH_PRICING.md` — Blizzard Auction House price tracking
