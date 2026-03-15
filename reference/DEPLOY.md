# Deployment & Operations Reference

> For day-to-day ops (restarting services, creating campaigns, etc.) see `docs/OPERATIONS.md`.
> This doc covers CI/CD, Docker environments, and server quirks relevant to development work.

---

## CI/CD — GitHub Actions

Three workflows, each with its own trigger:

| Workflow | Trigger | Target | Port |
|----------|---------|--------|------|
| `deploy-dev.yml` | Push to **any branch except main** | `dev.pullallthethings.com` | 8102 |
| `deploy-test.yml` | Push to **main** (PR merge) | `test.pullallthethings.com` | 8101 |
| `deploy-prod.yml` | **Version tag** (`v*`) | `pullallthethings.com` | 8100 |

- SSH key: `DEPLOY_SSH_KEY` secret in GitHub repo (ed25519, authorized on server)
- Deploy steps: git fetch/checkout → docker build → `docker compose up -d` → health check
- **CRITICAL: Never push a version tag without explicit permission from Mike.**

## Branch Strategy

- Feature branches → push → dev auto-deploys
- Merge to main → test auto-deploys
- Tag release (`git tag v1.x.x && git push --tags`) → prod deploys
- Single developer — no PR review required, but the CI gates enforce environment promotion

## Docker Environments

All three environments run as Docker containers on Hetzner (`5.78.114.224`):

```
/opt/guild-portal/
├── docker-compose.guild.yml   ← 3-env compose file
├── .env                       ← environment variables
└── ...
```

- `guild-portal-app-prod-1` / `guild-portal-db-prod-1` — prod, port 8100
- `guild-portal-app-test-1` / `guild-portal-db-test-1` — test, port 8101
- `guild-portal-app-dev-1`  / `guild-portal-db-dev-1`  — dev, port 8102
- Dev and test are behind nginx basic auth (username: `admin`, passwords in `/etc/nginx/htpasswd/`)
- App entrypoint: `guild_portal.app:create_app` (factory pattern), `PYTHONPATH=/app/src`

### Useful Docker Commands (dev/test only)

```bash
ssh hetzner

# View logs
docker logs guild-portal-app-dev-1 -f

# Run a migration on dev
docker exec guild-portal-app-dev-1 alembic upgrade head

# Access dev DB
docker exec guild-portal-db-dev-1 psql -U guild_user guild_db_dev

# Restart dev app
docker compose -f /opt/guild-portal/docker-compose.guild.yml restart app-dev
```

---

## Known Deploy Quirks

### Chrome "GitHub 404" After Restart

If Chrome shows a GitHub Pages 404 immediately after a deployment:
- Chrome is serving a stale cached socket from when this repo used GitHub Pages
- **Fix:** `chrome://net-internals/#sockets` → **Flush socket pools** → reload
- Not a server problem — occasional, happens when deploys coincide with Chrome socket reuse

### CRITICAL: `/etc/hosts` Override on the Hetzner Server

> **Full migration checklist: `docs/SERVER-IP-MIGRATION.md`**

The server has a mandatory `/etc/hosts` entry forcing the domain to its own IP:

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
