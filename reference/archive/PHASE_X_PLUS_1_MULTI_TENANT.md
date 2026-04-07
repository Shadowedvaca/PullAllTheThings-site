# Phase X+1 — Multi-Tenant SaaS Architecture

> **Status:** DRAFT / Pre-planning
> **Depends on:** Phase X (SaaS Platform) deployed and generating revenue
> **Last updated:** 2026-03-17

This document covers converting the single-tenant guild platform into a proper multi-tenant SaaS backend. Phase X buys the runway (selling single-tenant instances while this gets built). Phase X+1 is the migration that makes the product economically viable at scale.

---

## Why the Current Architecture Can't Scale to 100 Tenants

The current server (Hetzner, 2 vCPU / 2GB RAM / 38GB disk) is already close to its limit running **3 app containers + 3 databases + sv-tools**:

| Container | RAM in use |
|---|---|
| guild-portal-app-prod | ~329 MB |
| guild-portal-app-test | ~43 MB |
| guild-portal-app-dev | ~71 MB |
| sv-tools | ~231 MB |
| 3× PostgreSQL | ~80 MB combined |
| **Total** | **~754 MB active** (1.3GB used, 1GB swap already in use) |

Projecting the single-tenant model to 100 instances:

| Resource | Per idle tenant | × 100 tenants |
|---|---|---|
| App container RAM | ~70 MB | **~7 GB** |
| PostgreSQL RAM | ~15–40 MB | **~1.5–4 GB** |
| Disk (app image) | ~300 MB | **~30 GB** (shared layers help, but not 100%) |
| Disk (DB data) | ~50–200 MB/tenant | **~5–20 GB** |
| **Realistic total RAM** | | **~10–12 GB minimum** |

**Verdict:** The current server handles maybe **10–15 idle tenants** before swap kills it. A much larger machine (e.g. Hetzner AX41, 64GB RAM, 6 cores, ~$80/month) could host 100 idle tenants, but at that server cost the economics only work if most tenants are dormant most of the time. Even then, one active guild raid night could spike 5–10 tenants simultaneously.

**The right answer is multi-tenancy: one app process, one database cluster, serving all tenants.**

---

## Target Architecture (End State)

```
Single server (or small cluster)
│
├── Nginx  ─── routes by subdomain ──► {tenant}.guildportal.gg
│
├── FastAPI app (one process, multi-tenant aware)
│   ├── Tenant middleware: resolves tenant from Host header
│   ├── Per-request DB connection from tenant's schema
│   └── Background scheduler: per-tenant job registry
│
├── PostgreSQL (one cluster)
│   ├── system.*         — tenants, billing, provisioning, global config
│   ├── tenant_{slug}.*  — full schema clone per tenant (isolated data)
│   └── (shared read-only reference tables optional)
│
└── Discord (single bot token)
    └── Bot is in N guild servers; routes events by guild_id → tenant lookup
```

---

## The Big Decisions

### 1. Database Isolation Strategy

Three options:

| Strategy | Isolation | Overhead | Migration complexity |
|---|---|---|---|
| Separate DB per tenant | ★★★ | High (current model) | Low — already done |
| Schema per tenant (same DB) | ★★ | Low | Medium — need tenant_id-aware connection routing |
| Shared tables + tenant_id column | ★ | Lowest | High — every query needs WHERE tenant_id = ? |

**Recommendation: Schema-per-tenant.**

- PostgreSQL schemas are cheap. Each tenant gets `tenant_{slug}.*` mirroring the current `common.*`, `patt.*`, `guild_identity.*` layout.
- No risk of cross-tenant data leakage from a missing WHERE clause.
- Alembic migrations run per-schema (manageable with a migration runner that iterates tenants).
- Connection pooling: a single PgBouncer pool, but connection strings switch `search_path` per request.
- Single-tenant instances (Phase X) can be migrated by importing their DB into a new schema in the shared cluster.

### 2. App Process Strategy

One FastAPI app, not N apps. The app resolves tenant context from the request's `Host` header (or a `X-Tenant-ID` header for API calls). A `TenantMiddleware` looks up the tenant record from `system.tenants`, sets a context variable (Python `contextvars.ContextVar`), and the DB layer uses it to set `search_path`.

```python
# Pseudocode — request lifecycle
async def dispatch(request, call_next):
    host = request.headers["host"]           # e.g. "myguild.guildportal.gg"
    tenant = await resolve_tenant(host)      # lookup in system.tenants
    set_tenant_context(tenant)               # ContextVar
    response = await call_next(request)
    return response

# DB session — sets search_path before query
async def get_db_session(tenant: Tenant):
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO tenant_{tenant.slug}, public"))
        yield conn
```

### 3. Discord Bot Strategy

**Key insight:** A single Discord bot application can be in thousands of servers simultaneously. Discord routes incoming events by `guild_id`. We already store `guild_id` per tenant — this becomes the routing key.

What changes:
- Instead of one bot token per tenant (current model), there is **one bot token for the platform**.
- The guild leader invites the platform bot to their server (OAuth2 bot invite — they click a link, still manual, still ToS-safe).
- All bot events (`on_member_join`, voice state, etc.) arrive in one process and are dispatched to the correct tenant by `guild_id` lookup.
- Per-tenant bot config (channel IDs, role IDs, feature flags) is loaded from `tenant_{slug}.discord_config` on demand, cached per guild.

What this eliminates:
- Per-tenant bot token management (currently encrypted in each tenant's `discord_config`)
- N separate bot processes
- The "register your own Discord application" step from the setup wizard (replaced by "invite our bot to your server" — much simpler for users)

**Trade-off:** All tenants share one bot identity (same username/avatar). Premium tiers could offer a custom bot name/avatar via a separate Discord application — but that's a stretch goal.

### 4. Blizzard API Strategy

Similar to Discord: one Blizzard API application, one OAuth client_id/secret, platform-managed. The per-character Battle.net OAuth tokens are still user-owned (users click "Connect Battle.net" themselves), but the API credentials belong to the platform.

This is actually simpler than the current per-tenant credential model and is ToS-compliant as long as users explicitly authorize their own tokens.

Rate limits: Blizzard's API rate limits are per-client-credentials app. At 100 tenants with background sync, this could be a concern. Mitigation: stagger scheduler jobs across tenants (already needed for background jobs anyway), use a central rate-limit token bucket.

---

## Sub-Phases

### Phase X+1.1 — System Schema & Tenant Registry

Add a `system` schema to the PostgreSQL cluster:

```sql
system.tenants          -- id, slug, subdomain, display_name, tier, status, created_at
system.tenant_billing   -- tenant_id, stripe_customer_id, stripe_sub_id, plan, billing_status
system.tenant_config    -- tenant_id, discord_guild_id, blizzard_region, etc.
system.provisioning_log -- tenant_id, step, status, error, occurred_at
```

Provisioning API (internal, not public):
- `POST /system/tenants` — creates tenant record + schema + runs Alembic migrations for that schema
- `GET /system/tenants/{slug}/status` — provisioning health check
- `DELETE /system/tenants/{slug}` — deprovision (with data export trigger)

### Phase X+1.2 — Tenant Middleware & DB Routing

- `TenantMiddleware` in `app.py` — resolves tenant from Host header, 404s on unknown hosts
- `get_db_session()` becomes tenant-aware (sets `search_path`)
- All existing routes work unchanged — they just operate in the correct schema
- `ContextVar` pattern: no function signature changes needed across the codebase
- Dev/test: tenant slug injected via header or env var; PATT itself runs as tenant `patt`

### Phase X+1.3 — Shared Discord Bot

- Platform-level bot replaces per-tenant bot tokens
- `bot.py` refactored: event handlers look up tenant by `guild_id`, load tenant config, dispatch
- Per-tenant feature flags (enable_onboarding, enable_guild_quotes, etc.) loaded from tenant schema
- Bot invite link generated per tenant (includes their Discord `guild_id` as a pre-authorization hint)
- Admin UI: "Connect Discord" step becomes "Invite Bot to Your Server" (one-click OAuth2 invite link)
- Setup wizard step 2 simplified accordingly

### Phase X+1.4 — Shared Blizzard API Credentials

- Remove per-tenant `site_config.blizzard_client_id/secret_encrypted`
- Platform-level Blizzard credentials in `system.platform_config`
- Setup wizard step 3 (Blizzard credentials) removed entirely — users still do per-character Battle.net OAuth, but no API app setup needed
- Rate-limit tracker: shared token bucket in Redis or in-memory (if single process) to stay under Blizzard limits across all tenant sync jobs

### Phase X+1.5 — Multi-Tenant Background Scheduler

- Scheduler currently boots once per app process (per-tenant instance).
- Needs to become a **tenant-aware job registry**: on startup, load all active tenants; register per-tenant instances of each recurring job (blizzard sync, bnet refresh, AH sync, etc.)
- Job context carries `tenant_slug` and uses it to set DB `search_path` for that job's session
- New tenants/deprovisioned tenants update the registry at runtime (no restart needed)
- Job concurrency: cap simultaneous scheduler jobs across tenants (don't run 100 blizzard syncs at once)

### Phase X+1.6 — Alembic Multi-Tenant Migration Runner

- Today: `alembic upgrade head` runs against one DB
- Multi-tenant: need a runner that iterates all tenant schemas and applies pending migrations
- Script: `scripts/migrate_all_tenants.py` — reads `system.tenants`, runs Alembic per schema
- New tenant provisioning runs migrations at creation time (already isolated)
- Must be idempotent and safe to re-run
- CI/CD: migration runner replaces the single `alembic upgrade head` step in deploy pipeline

### Phase X+1.7 — PATT Migration to Tenant Schema

The last step: PATT itself (the original guild) migrates from its current isolated DB into the multi-tenant cluster as `tenant_patt`.

- Export PATT data (uses Phase X.5 data portability tooling)
- Provision `tenant_patt` schema in shared cluster
- Import data
- Nginx update: `pullallthethings.com` → same app, Host header resolves to `patt` tenant
- Decommission standalone PATT DB containers
- **This is the cutover that finalizes the architecture.** Do last, after everything else is proven.

---

## Migration Path from Phase X (Single-Tenant Runway)

Phase X sells single-tenant instances (separate containers + DBs per customer, current model). Phase X+1 is the background migration. The two can coexist during the transition:

1. Phase X+1.1–.6 built and tested on a new staging environment
2. New customers provisioned directly into multi-tenant cluster (no more per-tenant containers)
3. Existing single-tenant customers migrated one at a time (data export → import into shared cluster → DNS cutover → old container decommissioned)
4. Phase X+1.7 migrates PATT itself last

There is no "big bang" cutover. Each tenant migrates independently. Old and new architecture coexist during the window.

---

## Infrastructure Changes

| Item | Phase X (single-tenant) | Phase X+1 (multi-tenant) |
|---|---|---|
| App containers | 1 per tenant | 1 shared (+ replicas if needed) |
| PostgreSQL | 1 DB per tenant | 1 cluster, schema per tenant |
| Discord bot | 1 bot token per tenant | 1 platform bot token |
| Blizzard API | 1 app per tenant (setup friction) | 1 platform app |
| Nginx | 1 server block per tenant | wildcard `*.guildportal.gg` → one upstream |
| SSL certs | 1 cert per tenant (Let's Encrypt) | 1 wildcard cert |
| Server needed | 64GB RAM for 100 tenants | 16–32GB RAM for 100 tenants |

For wildcard SSL on Hetzner, Let's Encrypt + Certbot with DNS-01 challenge (Hetzner DNS API plugin) handles `*.guildportal.gg` as a single cert.

---

## Estimated Scope

This is a large phase. Rough breakdown:

| Sub-phase | Complexity | Notes |
|---|---|---|
| X+1.1 System schema + provisioning API | Medium | New schema + scripts, no app changes |
| X+1.2 Tenant middleware + DB routing | Medium-High | Core plumbing; affects every request |
| X+1.3 Shared Discord bot | High | Bot refactor is non-trivial; per-guild event routing |
| X+1.4 Shared Blizzard credentials | Low-Medium | Remove per-tenant creds, centralize |
| X+1.5 Multi-tenant scheduler | High | Job registry + concurrency management |
| X+1.6 Alembic multi-tenant runner | Medium | Scripting + CI changes |
| X+1.7 PATT migration | Medium | Data migration + cutover |

Don't attempt this as one PR. Each sub-phase should be its own branch and can ship independently behind a feature flag if needed.

---

## Open Questions

1. **Redis**: The multi-tenant scheduler and rate-limit buckets would benefit from Redis as shared state (especially if the app ever runs as multiple replicas). Currently no Redis in the stack. Worth introducing in X+1.5 or earlier.
2. **Custom domains for Premium tier**: Wildcard subdomain is easy. Custom domains (e.g. `guild.myguildname.com`) require per-domain SSL certs and DNS validation. Certbot handles this but it's more ops work. Defer to post-launch.
3. **Tenant isolation hardening**: Schema-per-tenant protects against accidental data leakage from missing WHERE clauses, but not from a compromised `search_path`. At scale, consider row-level security (PostgreSQL RLS) as an additional layer.
4. **What happens when a tenant cancels?** — Data export triggered, schema archived (not immediately deleted), deleted after 30-day grace period. Define retention policy before building.
5. **Single vs multi-process app**: If the app stays single-process, one crashed tenant can affect others. Worth evaluating whether tenant groups should be isolated into separate processes (light-weight process-per-tier, not per-tenant).

---

## Related Files

- `reference/PHASE_X_SAAS_PLATFORM.md` — Phase X (single-tenant runway, prerequisite)
- `src/guild_portal/app.py` — where TenantMiddleware will be added
- `src/sv_common/db/` — DB session management (will need tenant-aware refactor)
- `src/sv_common/guild_sync/scheduler.py` — background scheduler (major refactor in X+1.5)
- `src/guild_portal/bot/` — Discord bot (major refactor in X+1.3)
- `alembic/` — migration infrastructure (X+1.6)
