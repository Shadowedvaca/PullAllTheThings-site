# Phase 4: Guild Portal — Multi-Guild Platform Release

## Vision

Transform the PATT guild platform from a single-guild, single-tenant application into
**Guild Portal** — a deployable, configurable platform any WoW guild can run. Ships with
Docker packaging, a first-run setup wizard, and integrations with Raider.IO, Warcraft Logs,
and Blizzard Auction House data on top of the existing Blizzard API and Discord bot.

**Product Name:** Guild Portal (working title — may change)

**Timeline:** 2 weeks (10 working days)

---

## Sub-Phases

| Phase | Name | Days | Migration | Depends On |
|-------|------|------|-----------|------------|
| 4.0 | Config Extraction & Genericization | 1–2 | 0031 | — |
| 4.1 | Setup Wizard | 2–4 | — | 4.0 |
| 4.2 | Docker & Environments | 3–5 | — | 4.0 |
| 4.3 | Blizzard API Expansion | 5–7 | 0032 | 4.0 |
| 4.4 | Raider.IO Integration | 7–8 | 0033 | 4.3 |
| 4.5 | Warcraft Logs Integration | 8–10 | 0034 | 4.0 |
| 4.6 | Auction House Pricing | 9–10 | 0035 | 4.3 |

### Dependency Graph

```
4.0 Config Extraction
 ├── 4.1 Setup Wizard
 ├── 4.2 Docker & Environments
 ├── 4.3 Blizzard API Expansion
 │    ├── 4.4 Raider.IO
 │    └── 4.6 AH Pricing
 └── 4.5 Warcraft Logs
```

Phases 4.4, 4.5, and 4.6 are independent of each other and can be worked in parallel
once their prerequisites are met.

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

| Integration | Auth | New DB Tables | Scheduler |
|-------------|------|---------------|-----------|
| Blizzard Raids/M+/Achievements | Existing Blizzard creds | `character_raid_progress`, `character_mythic_plus`, `character_achievements`, `progression_snapshots` | Added to Blizzard sync pipeline |
| Raider.IO | None (free, public) | `raiderio_profiles` | After Blizzard sync |
| Warcraft Logs | OAuth2 client credentials (per guild) | `wcl_config`, `character_parses`, `raid_reports` | Daily, independent pipeline |
| Blizzard AH | Existing Blizzard creds | `tracked_items`, `item_price_history` | Hourly for tracked items |

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
- In-game addon changes (PATTSync addon works as-is)

---

## Success Criteria

1. A new guild leader can go from `docker compose up` to a fully working guild portal
   in under 30 minutes using only the setup wizard and guided instructions
2. PATT production instance is unaffected throughout — no downtime, no data loss
3. Dev and test environments are live on subdomains with isolated databases
4. All existing tests pass (409+ unit tests)
5. Raider.IO M+ scores visible on roster page
6. Warcraft Logs parse data queryable for characters with uploaded logs
7. AH prices tracked and displayed for guild-configured items
8. Last-login optimization measurably reduces Blizzard API call volume

---

## File Index

- `reference/PHASE_4_0_CONFIG_EXTRACTION.md` — Hardcoded value extraction, genericization
- `reference/PHASE_4_1_SETUP_WIZARD.md` — First-run web-based setup wizard
- `reference/PHASE_4_2_DOCKER_ENVIRONMENTS.md` — Docker packaging, Caddy, dev/test/prod
- `reference/PHASE_4_3_BLIZZARD_EXPANSION.md` — Last-login optimization, raids, M+, achievements
- `reference/PHASE_4_4_RAIDERIO_INTEGRATION.md` — Raider.IO M+ and raid progression
- `reference/PHASE_4_5_WARCRAFT_LOGS.md` — Warcraft Logs parses, reports, attendance
- `reference/PHASE_4_6_AH_PRICING.md` — Blizzard Auction House price tracking
