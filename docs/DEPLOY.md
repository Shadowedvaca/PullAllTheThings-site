# Deployment & Operations Reference

> For day-to-day ops (restarting services, creating campaigns, etc.) see `docs/OPERATIONS.md`.
> This doc covers CI/CD, Docker environments, and server quirks relevant to development work.

---

## CI/CD — GitHub Actions

`reference/development-and-release.md` is authoritative for authorization,
promotion, versioning, release notes, evidence, and rollback. This section is
an operational inventory of implemented workflows:

| Workflow | Trigger | Target | Server |
|----------|---------|--------|--------|
| `deploy-dev.yml` | **Manual** (`gh workflow run deploy-dev.yml -f branch=X`) | `dev.pullallthethings.com` | `my-web-apps-dev` |
| `pull-request-validation.yml` | Pull request | Isolated GitHub runner/PostgreSQL | GitHub-hosted |
| `deploy-test.yml` | Approved push to **main** | `test.pullallthethings.com` | `my-web-apps-test` |
| `deploy-prod.yml` | Exact `prod-v*` tag, currently blocked by repository readiness interlock | `pullallthethings.com` | `hetzner` |
| `publish-release.yml` | Successful production deployment | GitHub Release | GitHub-hosted |

- SSH key: `DEPLOY_SSH_KEY` secret in GitHub repo (authorized on all three servers)
- Host secrets: `DEV_HOST`, `TEST_HOST`, `PROD_HOST` in GitHub repo secrets
- Deployment resolves and checks out an exact commit, builds, starts, verifies
  runtime version/environment/commit and database health, then verifies the
  Alembic head.
- Never push a production tag outside the Promotion to production gate.
- Production is not currently available: `.github/production-readiness.json`
  defaults to blocked until issues #54 and #55 are complete and their controls
  are explicitly verified and enabled in a reviewed repository change.
- Even after readiness is enabled, Production preflight must find a successful
  `Deploy to Test` run for the exact tag-target SHA. Containment in `main` is not
  sufficient evidence.

## Delivery authority

Branch, PR, test, and production flow is defined only in
`reference/work-management.md` and `reference/development-and-release.md`.
This runbook does not authorize merge, tags, deployment, environment changes,
or rollback.

---

## Docker Environments

Three environments on **three separate servers**. Dev and test are shared CX23 nodes.

### Prod — `hetzner`

```
/opt/guild-portal/
├── docker-compose.guild.yml   ← prod-only compose (app-prod + db-prod)
├── .env.prod
└── ...
```

- `guild-portal-app-prod-1` / `guild-portal-db-prod-1` — port 8100
- Nginx proxies `pullallthethings.com` → 8100

```bash
ssh hetzner

# View prod logs
docker logs guild-portal-app-prod-1 -f

# Run a migration on prod (only with explicit permission)
docker exec guild-portal-app-prod-1 alembic upgrade head

# Access prod DB (only with explicit permission)
docker exec guild-portal-db-prod-1 psql -U guild_user guild_db

# Restart prod app
docker compose -f /opt/guild-portal/docker-compose.guild.yml restart app-prod
```

### Dev — `my-web-apps-dev`

```
/opt/guild-portal/
├── docker-compose.dev.yml   ← single-env compose (app + db)
├── .env                     ← dev env vars; DB_PASSWORD must match db service
└── ...
```

- Service names: `app`, `db` — port 8100
- Nginx proxies `dev.pullallthethings.com` → 8100 (behind htpasswd auth)

```bash
ssh my-web-apps-dev

# View dev logs
docker compose -f /opt/guild-portal/docker-compose.dev.yml logs app -f

# Run a migration on dev
docker compose -f /opt/guild-portal/docker-compose.dev.yml exec app alembic upgrade head

# Access dev DB
docker compose -f /opt/guild-portal/docker-compose.dev.yml exec db psql -U guild_user guild_db

# Restart dev app
docker compose -f /opt/guild-portal/docker-compose.dev.yml restart app
```

### Test — `my-web-apps-test` (91.99.121.21)

Same layout as dev, using `docker-compose.test.yml` and `test.pullallthethings.com`.

```bash
ssh my-web-apps-test
docker compose -f /opt/guild-portal/docker-compose.test.yml logs app -f
```

---

## Known Deploy Quirks

### Chrome "GitHub 404" After Restart

If Chrome shows a GitHub Pages 404 immediately after a deployment:
- Chrome is serving a stale cached socket from when this repo used GitHub Pages
- **Fix:** `chrome://net-internals/#sockets` → **Flush socket pools** → reload
- Not a server problem — occasional, happens when deploys coincide with Chrome socket reuse

### CRITICAL: `/etc/hosts` Override on the Hetzner Prod Server

> **Full migration checklist: `docs/SERVER-IP-MIGRATION.md`**

The prod server has a mandatory `/etc/hosts` entry forcing the domain to its own IP:

```
5.78.114.224    pullallthethings.com www.pullallthethings.com
```

**Why:** After DNS migration from GitHub Pages, Google DNS served stale GitHub A records for 24+ hours.
Self-directed `curl` calls (health checks, smoke tests) hit GitHub 404s instead of the app.

**Why it's in two places:** `cloud-init` with `manage_etc_hosts: True` regenerates `/etc/hosts` from a
template on every boot. Entry lives in both `/etc/hosts` (active) and `/etc/cloud/templates/hosts.debian.tmpl` (survives reboots).

**If you change the server IP or migrate to a new server:** Update this entry before running any smoke tests.
See `docs/SERVER-IP-MIGRATION.md` for the full checklist.

---

## Local Development

```bash
# Create venv (first time)
python -m venv .venv

# Install dependencies
.venv/Scripts/pip install -r requirements.txt

# Run tests (unit only, no DB needed)
.venv/Scripts/pytest tests/unit/ -v

# Run dev server (requires .env with DATABASE_URL)
python scripts/run_dev.py
```

**Environment notes:**
- `JWT_SECRET_KEY` in `.env` must be 32+ bytes (PyJWT warns if shorter)
- DB-dependent tests require `TEST_DATABASE_URL` pointing to a running PostgreSQL instance
- Pure unit tests (smoke + pure function tests) pass without a live database
- `scheduler.py` contains emoji (🗑️) — always open with `encoding="utf-8"` in tests/scripts
