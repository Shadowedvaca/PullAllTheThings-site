# PATT Guild Platform — Shared AI Context

This file is authoritative for durable project identity, architecture,
dependencies, integrations, and repository constraints. It intentionally does
not contain release history or duplicate deployment procedure.

## Project identity

- **Project:** Pull All The Things (PATT) Guild Platform
- **Repository:** `Shadowedvaca/PullAllTheThings-site`
- **Domain:** `pullallthethings.com`
- **Owner:** Mike
- **Purpose:** Member and officer platform for the Pull All The Things World of
  Warcraft guild, with a real-life-first and zero-toxicity community model.
- **Companion:** Salt All The Things podcast.

## Product and architecture

The FastAPI application provides guild identity and authentication, voting,
member and officer pages, roster and raid tools, Discord automation, Blizzard
data synchronization, crafting data, and background jobs. It includes:

- `src/guild_portal/`: FastAPI application, routes, Jinja templates, static
  assets, middleware, and application services;
- `src/sv_common/`: shared authentication, database, Discord, identity,
  notification, configuration, encryption, and guild-sync services;
- `alembic/versions/`: ordered PostgreSQL schema and data migrations;
- `wow_addon/GuildSync/`: World of Warcraft GuildSync addon; and
- `companion_app/`: local GuildSync watcher.

The application runs in Docker behind Nginx. Development, test, and production
are isolated server/database environments. The entrypoint applies Alembic
migrations before starting Uvicorn. Environment and promotion semantics are
owned by `reference/development-and-release.md`; operational inventory and
server-specific cautions live in `docs/DEPLOY.md` and `docs/BACKUPS.md`.

## Technology and integrations

| Area | Technology or service |
|---|---|
| Runtime | Python 3.11+, FastAPI, Uvicorn, Jinja2 |
| Data | PostgreSQL 16, SQLAlchemy 2, asyncpg, Alembic |
| Authentication | JWT, bcrypt, invite-code registration, Battle.net OAuth |
| Guild integrations | Discord API/bot, Blizzard API, Raider.IO, Warcraft Logs |
| Data sources | Wowhead, Archon, Icy Veins, Method and configured guild sources |
| Scheduling | APScheduler and application background tasks |
| Testing | pytest, pytest-asyncio, httpx, isolated PostgreSQL where required |
| Delivery | Docker Compose and GitHub Actions |

External calls must be bounded, observable, and safe to retry where possible.
Credentials belong in environment/secret stores or encrypted application
configuration, never source, logs, issues, evidence, or release notes.

## Data model constraints

- `guild_identity.players` is the central guild identity entity.
- `players.discord_user_id` references `discord_users.id`; `discord_users` has
  no `player_id` column.
- Legacy `common.guild_members` and `common.characters` were removed. Do not
  reintroduce dependencies on them.
- Discord channel configuration belongs in `common.discord_config`, not source
  or hard-coded environment defaults.
- `common.site_config` is loaded into `sv_common.config_cache`; use the cache in
  application modules.
- `enrichment.*` data is rebuildable and may be truncated by stored procedures;
  stable user-owned tables must not depend on it as an FK target.
- Gear-plan weapon slots use `main_hand_2h` and `main_hand_1h`, not
  `main_hand`.
- Read `docs/SCHEMA.md` and the relevant migrations before changing queries or
  schema behavior.

## Application constraints

- Admin pages extend `base_admin.html`; public/member pages use their relevant
  shared base templates.
- Preserve the dark fantasy design system and CSS custom properties documented
  in `docs/DESIGN.md`.
- API behavior should use the established `{"ok": ..., "data": ...}` or
  `{"ok": false, "error": ...}` shapes unless a versioned contract says
  otherwise.
- User-facing failures must be safe and useful; technical context belongs in
  bounded logs and error reporting.
- Use transactions with explicit rollback behavior for multi-step writes.
- Preserve unrelated worktree changes and stop on conflicts that risk data,
  issues, releases, infrastructure, secrets, or production.

## Canonical references

- `reference/work-management.md`: Solo Development and delivery workflow.
- `reference/development-and-release.md`: quality, environments, versions,
  releases, promotion, migrations, health, rollback, and evidence.
- `docs/ARCHITECTURE.md`: detailed route and component inventory.
- `docs/SCHEMA.md`: current schema reference; verify against migrations.
- `docs/DESIGN.md`: UI design language.
- `docs/DEPLOY.md`, `docs/BACKUPS.md`, and `docs/OPERATIONS.md`: operational
  inventory and runbooks subordinate to the canonical release standard.
