# Infrastructure Scaling: Single-Tenant vs Multi-Tenant

> **Status:** Reference / Pre-launch planning
> **Last updated:** 2026-03-17
> **Context:** Current server benchmarks + scaling projections for the SaaS product

This document exists so you can make informed decisions about when to upgrade hardware, when to invest in multi-tenant architecture, and what the actual economics look like at each stage.

---

## Current Server Baseline (as of 2026-03-17)

**Server:** Hetzner vServer (CX11 or equivalent)

| Resource | Spec | Notes |
|---|---|---|
| CPU | 2× AMD EPYC-Rome vCPU | Shared cores |
| RAM | 2 GB | 1.3GB used, 538MB free, 1GB swap in use |
| Disk | 38 GB SSD | 15GB used, 22GB free |
| Swap | 2 GB | Already 1GB consumed — a warning sign |

**Current container memory footprint:**

| Container | RAM in use |
|---|---|
| guild-portal-app-prod | ~329 MB (active — scheduler, bot, connection pool) |
| guild-portal-app-test | ~43 MB (idle) |
| guild-portal-app-dev | ~71 MB (idle) |
| sv-tools | ~231 MB |
| guild-portal-db-prod | ~44 MB |
| guild-portal-db-test | ~14 MB |
| guild-portal-db-dev | ~22 MB |
| **Total in use** | **~754 MB active** (but 1.3GB used with OS/buffers) |

**Other key measurements:**
- PostgreSQL prod DB size on disk: **90 MB** (31 MB live data, rest is PostgreSQL overhead)
- App Docker image (on disk): **686 MB** — but all 3 app containers share the same image layers, so adding more containers barely costs disk
- PostgreSQL image: **395 MB** — same deal, shared across DB containers
- CPU load average: **0.32 / 0.19 / 0.12** — effectively idle
- PostgreSQL `max_connections`: **100** (default, not tuned)
- Active connections on prod DB: **11** (from the app's asyncpg pool)

---

## Single-Tenant Model: What Each Instance Costs

In the single-tenant model, each customer gets their own app container + their own PostgreSQL container. Completely isolated, mirroring how PATT runs today.

**Per-tenant resource cost:**

| Resource | Idle tenant | Active tenant (raid night) | Notes |
|---|---|---|---|
| App RAM | ~75–100 MB | ~300–350 MB | Scheduler, bot, connection pool inflate at activity |
| DB RAM | ~20–44 MB | ~44–60 MB | PostgreSQL buffers grow with query activity |
| **Total RAM** | **~100–150 MB** | **~350–400 MB** | |
| Disk (DB data) | ~50–100 MB | ~100–200 MB | Grows slowly; PATT is 90MB after years of use |
| Disk (app image) | ~0 MB extra | ~0 MB extra | Layers shared; only container diff layer (~5MB) added |
| CPU | Negligible at idle | 1–5% per core | Scheduler jobs fire every 30min; bot events |

**Realistic average:** Most guilds are idle 22+ hours/day. Raid nights are 2–4 hours, 2–3 nights/week. Assume **~175 MB average RAM per tenant** when blending idle vs. active time.

---

## Single-Tenant: Server Capacity by Machine Size

| Server | RAM | ~Monthly cost (Hetzner) | Comfortable tenant ceiling | Notes |
|---|---|---|---|---|
| **Current (CX11)** | 2 GB | ~$4/mo | **7–8 tenants** | Already in swap. Don't sell on this. |
| CX21 | 4 GB | ~$6/mo | 15–18 tenants | Bare minimum for selling; not much headroom |
| **CX31** | **8 GB** | **~$12/mo** | **35–40 tenants** | Good launch server. Upgrade before first sale. |
| CX41 | 16 GB | ~$23/mo | 70–80 tenants | Comfortable runway through the single-tenant phase |
| CX51 | 32 GB | ~$40/mo | 150+ tenants | Overkill for single-tenant; more relevant for multi |
| CCX43 (dedicated vCPU) | 32 GB | ~$80/mo | 150+ tenants | Better CPU isolation; worth it at 50+ active guilds |

> Hetzner pricing approximate as of early 2026. Always verify at hetzner.com/cloud.

**Ceiling calculation methodology:**
- Reserve ~1 GB for OS + Nginx + overhead
- Reserve ~300 MB for PATT itself (your own instance)
- Remaining RAM ÷ 175 MB average per tenant = comfortable ceiling
- "Comfortable" means ~20% RAM headroom left; don't fill to 100%

**Recommendation:** Upgrade to CX31 ($12/mo) before the first paying tenant. At $10/month per guild, your first 2 tenants cover the server. CX41 gives you the runway to hit 70 guilds without touching infrastructure again during the single-tenant phase.

---

## Single-Tenant: The Blockers (Ranked)

### 1. RAM — The Hard Limit

RAM runs out first, always. Unlike CPU (which just slows things down), running out of RAM causes swap thrashing, which causes the app to become unresponsive. A guild trying to use the site during a raid night while the server is swap-thrashing is a support nightmare.

**Signal to watch:** When free RAM (excluding buffers/cache) drops below 500 MB, it's time to either upgrade or stop selling until multi-tenant is ready.

### 2. CPU — The Soft Limit

Two vCPU cores is thin once you have multiple schedulers firing simultaneously. Each tenant runs:
- Blizzard roster sync (periodic)
- Battle.net character refresh (3:15 AM UTC daily)
- AH pricing sync (periodic)
- WCL sync (periodic)
- Attendance processing (every 30 min)

If 10 guilds all have Blizzard sync fire at the same time, you get a CPU spike. Mitigation: stagger job times at provisioning (e.g., offset each tenant's sync by 2–3 minutes). This is cheap to implement and buys a lot of headroom.

A CX41 (4 vCPU) eliminates most CPU concerns for the single-tenant phase.

### 3. Port Management — Operational Complexity

Each app container needs a unique host port (current: 8100 prod, 8101 test, 8102 dev). For N tenants, you need N ports, and Nginx must route by subdomain to the right one. This is scripted and not a hard limit (ports go up to 65535), but it adds provisioning complexity that needs automating before you can sell.

This is a **solved problem** — just needs a provisioning script.

### 4. Disk — Not a Real Problem

Docker image layers are shared. All N app containers share the same 686MB image on disk. The per-container overhead is ~5 MB (writable diff layer). DB data grows slowly — PATT after years of use is only 90 MB. Even at 100 tenants, disk is ~10–15 GB total. The current 38 GB disk handles this for a long time.

### 5. PostgreSQL `max_connections` — Not a Problem (Single-Tenant)

Each tenant has their own PostgreSQL container with its own `max_connections=100`. These limits are completely independent. Not a concern until multi-tenant (where all tenants share one DB).

---

## Multi-Tenant Model: What Changes

In the multi-tenant model: one app process, one PostgreSQL cluster, one Discord bot, serving all tenants. Each tenant gets an isolated PostgreSQL **schema** (`tenant_{slug}.*`) instead of an isolated database container.

**Per-tenant resource cost in multi-tenant:**

| Resource | Cost | Notes |
|---|---|---|
| App RAM | ~0 MB marginal | All tenants share one process; per-tenant overhead is ~1–5 MB for in-memory state |
| DB RAM | ~5–15 MB marginal | PostgreSQL caches schema metadata; shared buffer pool |
| Disk (DB) | ~50–100 MB per tenant | Same data, just in a schema instead of a separate DB |
| CPU | Low marginal | Shared scheduler with staggered jobs; one event loop |

**100 tenants on multi-tenant:**
- Estimated RAM: ~4–6 GB (one app process at scale + PostgreSQL shared buffers)
- vs. single-tenant: ~17–20 GB for the same 100 tenants
- **~3–4× more RAM-efficient**

**Multi-tenant server recommendation for 100 tenants:**

| Server | RAM | Cost | Notes |
|---|---|---|---|
| CX41 | 16 GB | ~$23/mo | Comfortable for 100 tenants |
| CX51 | 32 GB | ~$40/mo | Comfortable for 300+ tenants |

**New constraints that appear in multi-tenant:**

| Constraint | Detail | Mitigation |
|---|---|---|
| PostgreSQL `max_connections` | Now shared across ALL tenants. At 10 conns/tenant × 100 tenants = 1000 needed; default max is 100 | **PgBouncer** connection pooler in front of PostgreSQL. Standard practice, adds ~1 week of work |
| Schema migration complexity | Alembic must run against every tenant schema on each deploy | Migration runner script iterates schemas; adds ~5 min to deploy pipeline |
| Blast radius | One app bug or crash affects all tenants, not just one | Blue/green deploys; circuit breakers; thorough testing |
| Tenant data isolation | Must never leak data between tenants | `search_path` isolation + integration tests that verify cross-tenant queries return nothing |
| Discord bot scaling | One bot in potentially hundreds of servers | Discord supports this natively; no practical limit for our scale |

---

## Economics: Single-Tenant Runway

Scenario: You upgrade to CX41 ($23/mo) and sell at $15/month per guild.

| Tenants | Revenue/mo | Server cost | Margin | Notes |
|---|---|---|---|---|
| 0 | $0 | $23 | -$23 | Pre-launch |
| 2 | $30 | $23 | +$7 | Break-even on server |
| 10 | $150 | $23 | +$127 | Comfortable |
| 25 | $375 | $23 | +$352 | |
| 50 | $750 | $23 | +$727 | CX41 still handles this |
| 70 | $1,050 | $23 | +$1,027 | Approaching ceiling; start multi-tenant work |
| 70 + CX51 upgrade | $1,050 | $40 | +$1,010 | Buys more time if multi-tenant not ready |

The development investment for multi-tenant (Phase X+1) is substantial — months of work. The CX41 runway means you don't need to tackle it until the product is proven and generating real revenue. At 50–60 tenants, you'll know whether multi-tenant is worth it.

---

## The Decision Tree

```
Before first sale:
  └── Upgrade to CX31 ($12/mo) or CX41 ($23/mo)
      └── Automate provisioning (new container + DB + Nginx block + subdomain)

At 10–15 tenants:
  └── Are you selling consistently?
      ├── No  → Keep going, no infra changes needed
      └── Yes → Start Phase X+1 planning; don't wait until you're full

At 50–60 tenants (CX41):
  └── RAM headroom < 20%?
      ├── No  → Keep going
      └── Yes → Either upgrade to CX51 (buys 6–12 more months)
                 OR cut over to multi-tenant (right answer if revenue justifies it)

At 70+ tenants:
  └── Multi-tenant is the only scalable path.
      Single-tenant server costs become the limiting factor on margin.
```

---

## Operational Things to Automate Before First Sale

These are the manual steps today that need scripts before you can onboard customers without touching the server by hand:

1. **Tenant provisioning script** — creates Docker network, app container, DB container, runs Alembic migrations, assigns port
2. **Nginx config generation** — adds subdomain block for `{slug}.guildportal.gg`, reloads Nginx
3. **Wildcard SSL cert** — Let's Encrypt wildcard for `*.guildportal.gg` (DNS-01 challenge via Hetzner DNS API) — one cert, covers all subdomains without per-tenant cert generation
4. **Scheduler offset assignment** — give each tenant a unique time offset for their background jobs to prevent thundering herd on Blizzard API
5. **Deprovisioning script** — data export trigger → archive container + volume → DNS removal → Nginx cleanup

---

## What the Multi-Tenant Migration Actually Requires

Captured in detail in `PHASE_X1_MULTI_TENANT.md`. Summary of the major work items:

| Work Item | Rough Size | Why It's Hard |
|---|---|---|
| Tenant middleware + DB schema routing | ~1–2 weeks | Touches every request path; needs thorough testing |
| Shared Discord bot (one token, N guilds) | ~2–3 weeks | Complete bot.py refactor; per-guild event routing |
| Multi-tenant background scheduler | ~2–3 weeks | Job registry with per-tenant context; concurrency limits |
| Alembic multi-tenant migration runner | ~1 week | Schema iteration + CI/CD changes |
| PgBouncer setup | ~3 days | New infra component; connection pooling config |
| Shared Blizzard API credentials | ~3 days | Remove per-tenant creds; centralize rate limiting |
| PATT itself migrated to tenant schema | ~1 week | Data migration + cutover; do this last |

**Total realistic estimate:** 2–4 months of part-time development. This is not a sprint — it's an architectural rewrite of the plumbing. The single-tenant runway exists precisely so this doesn't have to happen before you can make money.

---

## Related Documents

- `reference/PHASE_X_SAAS_PLATFORM.md` — Phase X overview (player dashboard, purchase flow, tier design)
- `reference/PHASE_X1_MULTI_TENANT.md` — Phase X+1 detail (multi-tenant architecture)
- `reference/DEPLOY.md` — current Docker + Nginx deployment setup
